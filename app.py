from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import os
from datetime import datetime, timedelta

import boto3
import uuid
import logging

app = Flask(__name__)
app.secret_key = os.urandom(24)

# --- AWS Configuration ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# AWS Region (best practice: use environment variables or IAM roles on EC2)
AWS_REGION = os.environ.get('AWS_REGION', 'ap-south-1') # e.g., 'us-east-1', 'ap-south-1'

# Initialize Boto3 clients and resources for DynamoDB and SNS.
# When running on an EC2 instance with an associated IAM Role, boto3 will automatically pick up credentials from the instance metadata.
# Therefore, you do NOT need to provide aws_access_key_id or aws_secret_access_key here.
try:
    dynamodb = boto3.resource(
        'dynamodb',
        region_name=AWS_REGION
    )
    sns_client = boto3.client(
        'sns',
        region_name=AWS_REGION
    )

    # Define DynamoDB table objects.
    
    USERS_TABLE = dynamodb.Table('medtrack_users')
    APPOINTMENTS_TABLE = dynamodb.Table('medtrack_appointments')
    PRESCRIPTIONS_TABLE = dynamodb.Table('medtrack_prescriptions')
    MEDICATION_REMINDERS_TABLE = dynamodb.Table('medtrack_medication_reminders')
    logger.info("Boto3 clients and DynamoDB tables initialized successfully, assuming IAM Role credentials.")
except Exception as e:
    logger.error(f"FATAL ERROR: Failed to initialize Boto3 clients or access DynamoDB tables. "
                 f"Please check your AWS_REGION, IAM Role permissions, and table names: {e}")
    # In a production setup, you might want to gracefully exit or prevent the app from starting
    # if database connections cannot be established.

# SNS Topic ARN for notifications.
# replace this with the ARN of an existing SNS Topic in your AWS account.
# Subscribe emails/phone numbers to this topic to receive notifications.
SNS_TOPIC_ARN = os.environ.get('SNS_TOPIC_ARN', 'arn:aws:sns:YOUR_AWS_REGION:YOUR_ACCOUNT_ID:medtrack-notifications') # REPLACE WITH YOUR ACTUAL SNS TOPIC ARN

# --- Helper function to prepare DynamoDB items for Jinja2 templates ---
def serialize_doc(item):
    if item:
        new_item = item.copy()
        # Mapping DynamoDB primary/sort keys to a generic '_id' for Jinja2 compatibility if needed
        if 'appointment_id' in new_item:
            new_item['_id'] = new_item['appointment_id']
        elif 'reminder_id' in new_item:
            new_item['_id'] = new_item['reminder_id']
        elif 'prescription_id' in new_item:
            new_item['_id'] = new_item['prescription_id']
        # For users, 'email' is the primary key and can be used directly or mapped.
        return new_item
    return None

# --- Flask Routes ---

@app.route('/')
def index():
    if 'user_email' in session:
        if session['user_type'] == 'patient':
            return redirect(url_for('patient_dashboard'))
        elif session['user_type'] == 'doctor':
            return redirect(url_for('doctor_dashboard'))
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        user_type = request.form['user_type']

        if password != confirm_password:
            flash('Passwords do not match. Please try again.', 'error')
            return redirect(url_for('register'))

        try:
            response = USERS_TABLE.get_item(Key={'email': email})
            if 'Item' in response:
                flash('Email already registered. Please login or use a different email.', 'error')
                return redirect(url_for('register'))

            new_user = {
                'email': email,
                'name': name,
                'password': password,
                'user_type': user_type
            }

            if user_type == 'doctor':
                new_user['specialization'] = request.form.get('specialization', '')
                new_user['location'] = request.form.get('location', '')
                new_user['medical_license'] = request.form.get('medical_license', '')
            elif user_type == 'patient':
                new_user['age'] = request.form.get('age', '')
                new_user['gender'] = request.form.get('gender', '')

            USERS_TABLE.put_item(Item=new_user)
            flash('Account created successfully! Please login.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            logger.error(f"Error during user registration in DynamoDB: {e}")
            flash('An error occurred during registration. Please try again.', 'error')
            return redirect(url_for('register'))

    return render_template('signup.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        try:
            response = USERS_TABLE.get_item(Key={'email': email})
            user = response.get('Item')

            if user and user['password'] == password:
                session['user_email'] = user['email']
                session['username'] = user['name']
                session['user_type'] = user['user_type']
                flash(f'Welcome, {user["name"]}!', 'success')

                if user['user_type'] == 'patient':
                    return redirect(url_for('patient_dashboard'))
                elif user['user_type'] == 'doctor':
                    return redirect(url_for('doctor_dashboard'))
            else:
                flash('Invalid email or password.', 'error')
        except Exception as e:
            logger.error(f"Error during login from DynamoDB: {e}")
            flash('An error occurred during login. Please try again.', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('user_email', None)
    session.pop('username', None)
    session.pop('user_type', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))


@app.route('/patient_dashboard')
def patient_dashboard():
    if 'user_email' not in session or session['user_type'] != 'patient':
        flash('Unauthorized access. Please login as a patient.', 'error')
        return redirect(url_for('login'))

    patient_email = session['user_email']
    patient_name = session['username']

    user_appointments = []
    user_reminders = []
    user_prescriptions = []
    doctors_from_db = []

    try:
        appointments_response = APPOINTMENTS_TABLE.scan(FilterExpression=boto3.dynamodb.conditions.Attr('patient_email').eq(patient_email))
        user_appointments = [serialize_doc(apt) for apt in appointments_response.get('Items', [])]

        reminders_response = MEDICATION_REMINDERS_TABLE.scan(FilterExpression=boto3.dynamodb.conditions.Attr('patient_email').eq(patient_email))
        user_reminders = [serialize_doc(rem) for rem in reminders_response.get('Items', [])]

        prescriptions_response = PRESCRIPTIONS_TABLE.scan(FilterExpression=boto3.dynamodb.conditions.Attr('patient_email').eq(patient_email))
        user_prescriptions = [serialize_doc(pres) for pres in prescriptions_response.get('Items', [])]

        doctors_response = USERS_TABLE.scan(FilterExpression=boto3.dynamodb.conditions.Attr('user_type').eq('doctor'))
        for doc in doctors_response.get('Items', []):
            doc['medical_license'] = doc.get('medical_license', 'N/A')
            doctors_from_db.append(serialize_doc(doc))

        today = datetime.now().strftime('%Y-%m-%d')
        for reminder in user_reminders:
            if reminder.get('date') < today and reminder.get('status') == 'Pending':
                MEDICATION_REMINDERS_TABLE.update_item(
                    Key={'reminder_id': reminder['reminder_id']},
                    UpdateExpression="SET #s = :status",
                    ExpressionAttributeNames={'#s': 'status'},
                    ExpressionAttributeValues={':status': 'Missed'}
                )
                reminder['status'] = 'Missed'

            if reminder.get('frequency') and 'daily' in reminder['frequency'] and reminder.get('last_checked_date') != today:
                MEDICATION_REMINDERS_TABLE.update_item(
                    Key={'reminder_id': reminder['reminder_id']},
                    UpdateExpression="SET taken_today = :false, #s = :pending, last_checked_date = :today",
                    ExpressionAttributeNames={'#s': 'status'},
                    ExpressionAttributeValues={
                        ':false': False,
                        ':pending': 'Pending',
                        ':today': today
                    }
                )
                reminder['taken_today'] = False
                reminder['status'] = 'Pending'
                reminder['last_checked_date'] = today

    except Exception as e:
        logger.error(f"Error fetching patient dashboard data from DynamoDB: {e}")
        flash('An error occurred while loading dashboard data. Please try again later.', 'error')

    return render_template(
        'patient_dashboard.html',
        username=patient_name,
        appointments=user_appointments,
        doctors_data=doctors_from_db,
        medication_reminders=user_reminders,
        prescriptions=user_prescriptions
    )


@app.route('/doctor_dashboard')
def doctor_dashboard():
    if 'user_email' not in session or session['user_type'] != 'doctor':
        flash('Please log in to access the doctor dashboard.', 'error')
        return redirect(url_for('login'))

    doctor_email = session['user_email']
    doctor_name = session['username']

    doctor_appointments = []
    doctor_prescriptions = []

    try:
        appointments_response = APPOINTMENTS_TABLE.scan(FilterExpression=boto3.dynamodb.conditions.Attr('doctor_name').eq(doctor_name))
        doctor_appointments = [serialize_doc(apt) for apt in appointments_response.get('Items', [])]

        prescriptions_response = PRESCRIPTIONS_TABLE.scan(FilterExpression=boto3.dynamodb.conditions.Attr('doctor_name').eq(doctor_name))
        doctor_prescriptions = [serialize_doc(pres) for pres in prescriptions_response.get('Items', [])]

    except Exception as e:
        logger.error(f"Error fetching doctor dashboard data from DynamoDB: {e}")
        flash('An error occurred while loading dashboard data. Please try again later.', 'error')

    return render_template('doctor_dashboard.html',
                            username=doctor_name,
                            appointments=doctor_appointments,
                            prescriptions=doctor_prescriptions)


@app.route('/book_appointment', methods=['POST'])
def book_appointment():
    if 'user_email' not in session or session['user_type'] != 'patient':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))

    patient_email = session['user_email']
    patient_name = session['username']

    doctor_name = request.form['doctor_name']
    appointment_date = request.form['appointment_date']
    appointment_time = request.form['appointment_time']
    reason = request.form['reason']

    try:
        new_appointment = {
            'appointment_id': str(uuid.uuid4()),
            'patient_email': patient_email,
            'patient_name': patient_name,
            'doctor_name': doctor_name,
            'date': appointment_date,
            'time': appointment_time,
            'reason': reason,
            'status': 'Pending'
        }
        APPOINTMENTS_TABLE.put_item(Item=new_appointment)

        message = (f"New appointment booked: Patient {patient_name} ({patient_email}) "
                   f"with Dr. {doctor_name} on {appointment_date} at {appointment_time} "
                   f"for reason: {reason}.")
        try:
            sns_client.publish(TopicArn=SNS_TOPIC_ARN, Message=message, Subject="New Medtrack Appointment")
            logger.info(f"SNS notification sent for new appointment: {new_appointment['appointment_id']}.")
        except Exception as sns_e:
            logger.error(f"Failed to send SNS notification for appointment booking: {sns_e}")

        flash('Appointment booked successfully! Awaiting doctor\'s approval.', 'success')
        return redirect(url_for('patient_dashboard', section='patient-appointments-section'))
    except Exception as e:
        logger.error(f"Error booking appointment to DynamoDB: {e}")
        flash('An error occurred while booking the appointment. Please try again.', 'error')
        return redirect(url_for('patient_dashboard', section='patient-book-appointment-section'))


@app.route('/cancel_appointment/<appointment_id>')
def cancel_appointment(appointment_id):
    if 'user_email' not in session or session['user_type'] != 'patient':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))

    patient_email = session['user_email']

    try:
        response = APPOINTMENTS_TABLE.get_item(Key={'appointment_id': appointment_id})
        appointment = response.get('Item')

        if appointment and appointment['patient_email'] == patient_email:
            if appointment['status'] not in ['Cancelled', 'Completed']:
                APPOINTMENTS_TABLE.update_item(
                    Key={'appointment_id': appointment_id},
                    UpdateExpression="SET #s = :status",
                    ExpressionAttributeNames={'#s': 'status'},
                    ExpressionAttributeValues={':status': 'Cancelled'}
                )

                message = (f"Appointment cancelled: Patient {patient_email}'s appointment "
                           f"with Dr. {appointment['doctor_name']} on {appointment['date']} "
                           f"at {appointment['time']} has been cancelled.")
                try:
                    sns_client.publish(TopicArn=SNS_TOPIC_ARN, Message=message, Subject="Medtrack Appointment Cancelled")
                    logger.info(f"SNS notification sent for appointment cancellation: {appointment_id}.")
                except Exception as sns_e:
                    logger.error(f"Failed to send SNS notification for cancellation: {sns_e}")

                flash('Appointment cancelled successfully.', 'success')
            else:
                flash(f"Appointment cannot be cancelled as its current status is '{appointment['status']}'.", 'error')
        else:
            flash('Appointment not found or you do not have permission to cancel it.', 'error')
    except Exception as e:
        logger.error(f"Error cancelling appointment in DynamoDB: {e}")
        flash('An error occurred while cancelling the appointment. Please try again.', 'error')

    return redirect(url_for('patient_dashboard', section='patient-appointments-section'))


@app.route('/update_appointment_status', methods=['POST'])
def update_appointment_status():
    if 'user_email' not in session or session['user_type'] != 'doctor':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))

    appointment_id = request.form['appointment_id']
    new_status = request.form['status']

    try:
        response = APPOINTMENTS_TABLE.get_item(Key={'appointment_id': appointment_id})
        appointment = response.get('Item')

        if appointment and appointment['doctor_name'] == session['username']:
            APPOINTMENTS_TABLE.update_item(
                Key={'appointment_id': appointment_id},
                UpdateExpression="SET #s = :status",
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':status': new_status},
                ReturnValues="UPDATED_NEW"
            )
            flash(f'Appointment status updated to {new_status}.', 'success')

            message = (f"Your appointment with Dr. {appointment['doctor_name']} "
                       f"on {appointment['date']} at {appointment['time']} has been updated to: {new_status}.")
            try:
                sns_client.publish(TopicArn=SNS_TOPIC_ARN, Message=message, Subject="Medtrack Appointment Update")
                logger.info(f"SNS notification sent for appointment status update: {appointment_id}.")
            except Exception as sns_e:
                logger.error(f"Failed to send SNS notification for status update: {sns_e}")
        else:
            flash('Appointment not found or you do not have permission to update it.', 'error')
    except Exception as e:
        logger.error(f"Error updating appointment status in DynamoDB: {e}")
        flash('An error occurred while updating appointment status. Please try again.', 'error')

    return redirect(url_for('doctor_dashboard', section='doctor-appointments-section'))


@app.route('/add_medication_reminder', methods=['POST'])
def add_medication_reminder():
    if 'user_email' not in session or session['user_type'] != 'patient':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))

    patient_email = session['user_email']
    medication = request.form['medication']
    dosage = request.form['dosage']
    frequency = request.form['frequency']
    times = request.form.getlist('times[]')
    start_date_str = request.form['start_date']
    end_date_str = request.form.get('end_date')
    prescribed_by = request.form.get('prescribed_by')
    instructions = request.form.get('instructions')
    is_active = request.form.get('is_active') == 'on'

    try:
        datetime.strptime(start_date_str, '%Y-%m-%d').date()
        if end_date_str:
            datetime.strptime(end_date_str, '%Y-%m-%d').date()
    except ValueError:
        flash('Invalid date format for reminder. Please use YYYY-MM-DD.', 'error')
        return redirect(url_for('patient_dashboard', section='patient-medication-reminders-section'))

    try:
        new_reminder = {
            'reminder_id': str(uuid.uuid4()),
            'patient_email': patient_email,
            'medication': medication,
            'dosage': dosage,
            'frequency': frequency,
            'times': times,
            'date': start_date_str,
            'end_date': end_date_str if end_date_str else None,
            'prescribed_by': prescribed_by if prescribed_by else None,
            'instructions': instructions if instructions else None,
            'is_active': is_active,
            'status': 'Upcoming',
            'taken_today': False,
            'last_checked_date': datetime.now().strftime('%Y-%m-%d')
        }
        MEDICATION_REMINDERS_TABLE.put_item(Item=new_reminder)

        message = (f"New medication reminder set: {medication} ({dosage}) "
                   f"at {', '.join(times)} starting {start_date_str} (Frequency: {frequency.capitalize()}).")
        try:
            sns_client.publish(TopicArn=SNS_TOPIC_ARN, Message=message, Subject="Medtrack Medication Reminder Set")
            logger.info(f"SNS notification sent for new medication reminder: {new_reminder['reminder_id']}.")
        except Exception as sns_e:
            logger.error(f"Failed to send SNS notification for reminder creation: {sns_e}")

        flash(f'Medication reminder for {medication} added successfully!', 'success')
        return redirect(url_for('patient_dashboard', section='patient-medication-reminders-section'))
    except Exception as e:
        logger.error(f"Error adding medication reminder to DynamoDB: {e}")
        flash('An error occurred while adding the reminder. Please try again.', 'error')
        return redirect(url_for('patient_dashboard', section='patient-medication-reminders-section'))

@app.route('/mark_reminder_taken/<reminder_id>', methods=['POST'])
def mark_reminder_taken(reminder_id):
    if 'user_email' not in session or session['user_type'] != 'patient':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))

    patient_email = session['user_email']
    action = request.form.get('action')

    try:
        response = MEDICATION_REMINDERS_TABLE.get_item(Key={'reminder_id': reminder_id})
        reminder = response.get('Item')

        if reminder and reminder['patient_email'] == patient_email:
            today = datetime.now().strftime('%Y-%m-%d')

            if 'last_checked_date' not in reminder:
                MEDICATION_REMINDERS_TABLE.update_item(
                    Key={'reminder_id': reminder_id},
                    UpdateExpression="SET last_checked_date = :today",
                    ExpressionAttributeValues={':today': today}
                )
                reminder['last_checked_date'] = today

            if reminder.get('frequency') and 'daily' in reminder['frequency'] and reminder['last_checked_date'] != today:
                MEDICATION_REMINDERS_TABLE.update_item(
                    Key={'reminder_id': reminder_id},
                    UpdateExpression="SET taken_today = :false, #s = :pending, last_checked_date = :today",
                    ExpressionAttributeNames={'#s': 'status'},
                    ExpressionAttributeValues={
                        ':false': False,
                        ':pending': 'Pending',
                        ':today': today
                    }
                )
                reminder['taken_today'] = False
                reminder['status'] = 'Pending'
                reminder['last_checked_date'] = today

            if action == 'take':
                if not reminder['taken_today']:
                    MEDICATION_REMINDERS_TABLE.update_item(
                        Key={'reminder_id': reminder_id},
                        UpdateExpression="SET taken_today = :true, #s = :taken",
                        ExpressionAttributeNames={'#s': 'status'},
                        ExpressionAttributeValues={
                            ':true': True,
                            ':taken': 'Taken'
                        }
                    )
                    flash(f"Medication '{reminder['medication']}' marked as taken for today.", 'success')
                else:
                    flash(f"Medication '{reminder['medication']}' already marked as taken today.", 'info')
            elif action == 'unmark':
                if reminder['taken_today']:
                    MEDICATION_REMINDERS_TABLE.update_item(
                        Key={'reminder_id': reminder_id},
                        UpdateExpression="SET taken_today = :false, #s = :pending",
                        ExpressionAttributeNames={'#s': 'status'},
                        ExpressionAttributeValues={
                            ':false': False,
                            ':pending': 'Pending'
                        }
                    )
                    flash(f"Medication '{reminder['medication']}' unmarked for today.", 'info')
                else:
                    flash(f"Medication '{reminder['medication']}' is already pending.", 'info')
        else:
            flash('Reminder not found or you do not have permission to update it.', 'error')
    except Exception as e:
        logger.error(f"Error marking reminder taken/pending in DynamoDB: {e}")
        flash('An error occurred while updating the reminder. Please try again.', 'error')

    return redirect(url_for('patient_dashboard', section='patient-medication-reminders-section'))


@app.route('/issue_prescription', methods=['POST'])
def issue_prescription():
    if 'user_email' not in session or session['user_type'] != 'doctor':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))

    doctor_name = session['username']
    patient_email = request.form['patient_email_prescribe']
    medication = request.form['medication']
    dosage = request.form['dosage']
    instructions = request.form['instructions']

    try:
        response = USERS_TABLE.get_item(Key={'email': patient_email})
        patient_user = response.get('Item')

        if not patient_user or patient_user.get('user_type') != 'patient':
            flash('Patient with this email does not exist or is not a patient user type.', 'error')
            return redirect(url_for('doctor_dashboard', section='doctor-issue-prescription-section'))

        new_prescription = {
            'prescription_id': str(uuid.uuid4()),
            'doctor_name': doctor_name,
            'patient_email': patient_email,
            'patient_name': patient_user['name'],
            'medication': medication,
            'dosage': dosage,
            'instructions': instructions,
            'date_prescribed': datetime.now().strftime('%Y-%m-%d')
        }
        PRESCRIPTIONS_TABLE.put_item(Item=new_prescription)

        message = (f"New prescription issued: Dr. {doctor_name} prescribed {medication} ({dosage}) "
                   f"for {patient_user['name']} ({patient_email}). Instructions: {instructions}")
        try:
            sns_client.publish(TopicArn=SNS_TOPIC_ARN, Message=message, Subject="Medtrack New Prescription")
            logger.info(f"SNS notification sent for new prescription: {new_prescription['prescription_id']}.")
        except Exception as sns_e:
            logger.error(f"Failed to send SNS notification for prescription: {sns_e}")

        flash(f'Prescription for {patient_user["name"]} issued successfully!', 'success')
        return redirect(url_for('doctor_dashboard', section='doctor-prescriptions-section'))
    except Exception as e:
        logger.error(f"Error issuing prescription to DynamoDB: {e}")
        flash('An error occurred while issuing the prescription. Please try again.', 'error')
        return redirect(url_for('doctor_dashboard', section='doctor-issue-prescription-section'))


@app.route('/delete_reminder/<reminder_id>')
def delete_reminder(reminder_id):
    if 'user_email' not in session or session['user_type'] != 'patient':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))

    patient_email = session['user_email']
    try:
        response = MEDICATION_REMINDERS_TABLE.get_item(Key={'reminder_id': reminder_id})
        reminder = response.get('Item')

        if reminder and reminder['patient_email'] == patient_email:
            MEDICATION_REMINDERS_TABLE.delete_item(Key={'reminder_id': reminder_id})
            flash('Medication reminder deleted successfully.', 'success')
        else:
            flash('Medication reminder not found or you do not have permission to delete it.', 'error')
    except Exception as e:
        logger.error(f"Error deleting reminder from DynamoDB: {e}")
        flash('An error occurred while deleting the reminder. Please try again.', 'error')
    return redirect(url_for('patient_dashboard', section='patient-medication-reminders-section'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)