from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import os
from datetime import datetime, timedelta
from pymongo import MongoClient
from bson.objectid import ObjectId # For working with MongoDB's default _id
# import boto3
# import logging
# import os
# import uuid
# from functools import wraps
# import smtplib
# from email.mime.multipart import MIMEMultipart
# from email.mime.text import MIMEText

app = Flask(__name__)
app.secret_key = os.urandom(24) 
# --- MongoDB Connection Setup ---
# This is YOUR MongoDB Atlas connection string.
# Ensure 'medtrack' is your database username and 'qqCBQdM50CjkpLvu' is its password.
# The 'appName=Cluster0' is optional but fine.
MONGO_URI = "mongodb+srv://medtrack:qqCBQdM50CjkpLvu@cluster0.kdttvoa.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
DB_NAME = "medtrack_db" # The name of your database in MongoDB Atlas

# Initialize the MongoDB client and select your database
client = MongoClient(MONGO_URI)
db = client[DB_NAME]

# Define your collections (these are like tables in a traditional database)
users_collection = db.users
appointments_collection = db.appointments
prescriptions_collection = db.prescriptions
medication_reminders_collection = db.medication_reminders

# --- Helper function to prepare MongoDB documents for Jinja2 templates ---
# MongoDB's _id is an ObjectId type, which Jinja2 cannot render directly.
# This function converts _id to a string so it can be used in templates (e.g., in URLs).
def serialize_doc(doc):
    if doc and '_id' in doc:
        # Create a copy to avoid modifying the original document from the cursor
        new_doc = doc.copy()
        new_doc['_id'] = str(new_doc['_id'])
        return new_doc
    return doc

# --- Flask Routes ---

@app.route('/')
def index():
    # Handles the root URL.Redirects logged-in users to their respective dashboards.Displays the landing page (index.html) for unauthenticated users.
    if 'user_email' in session:
        if session['user_type'] == 'patient':
            return redirect(url_for('patient_dashboard'))
        elif session['user_type'] == 'doctor':
            return redirect(url_for('doctor_dashboard'))
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    # Handles user registration (signup). Collects full name, email, password, user type (patient/doctor),
    # and specialization/location for doctors.Inserts new users into the 'users' collection.
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        user_type = request.form['user_type']
        
        # Specialization and location are only applicable for doctors
        specialization = request.form.get('specialization')
        location = request.form.get('location') # NEW: Added location for doctors

        if password != confirm_password:
            flash('Passwords do not match. Please try again.', 'error')
            return redirect(url_for('register'))

        # Check if email already exists in MongoDB users collection
        if users_collection.find_one({'email': email}):
            flash('Email already registered. Please login or use a different email.', 'error')
            return redirect(url_for('register'))
        
        # Create user document to be inserted into MongoDB
        new_user = {
            'name': name,
            'email': email,
            'password': password, # In a production app, hash this password (e.g., using bcrypt)
            'user_type': user_type
        }
        if user_type == 'doctor':
            new_user['specialization'] = specialization
            new_user['location'] = location # Add location to doctor's profile
            
        # Insert the new user document into the users collection
        users_collection.insert_one(new_user) # MongoDB will automatically add an _id field

        flash('Account created successfully! Please login.', 'success')
        return redirect(url_for('login')) # Corrected URL endpoint
    return render_template('register.html') # Ensure signup.html is renamed to register.html

@app.route('/login', methods=['GET', 'POST'])
def login():
    # Handles user login.Authenticates users against the 'users' collection.
    # Sets session variables and redirects to the appropriate dashboard.
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        # Find the user in the MongoDB users collection
        # For a real app, compare hashed passwords here: user = users_collection.find_one({'email': email}) and then check password_hash
        user = users_collection.find_one({'email': email, 'password': password})
        
        if user:
            # Set session variables
            session['user_email'] = user['email']
            session['username'] = user['name'] # Store full name in session for display
            session['user_type'] = user['user_type']
            # Store MongoDB's _id as a string in the session for later database queries
            session['user_db_id'] = str(user['_id']) 
            flash(f'Welcome, {user["name"]}!', 'success')

            # Redirect based on user type
            if user['user_type'] == 'patient':
                return redirect(url_for('patient_dashboard'))
            elif user['user_type'] == 'doctor':
                return redirect(url_for('doctor_dashboard'))
        else:
            flash('Invalid email or password.', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    # Clears session data and logs the user out, then redirects to the index page.
    # Clear all session variables
    session.pop('user_email', None)
    session.pop('username', None)
    session.pop('user_type', None)
    session.pop('user_db_id', None) # Also clear the database ID
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

@app.route('/patient_dashboard')
def patient_dashboard():
    # Displays the patient dashboard. Fetches patient's appointments, reminders, prescriptions.
    # Crucially, fetches and passes the list of registered doctors to the template.
    if 'user_email' not in session or session['user_type'] != 'patient':
        flash('Unauthorized access. Please login as a patient.', 'error')
        return redirect(url_for('login')) # Corrected URL endpoint

    patient_email = session['user_email']
    patient_name = session['username']
    
    # Fetch patient-specific data from MongoDB collections
    user_appointments = [serialize_doc(apt) for apt in appointments_collection.find({'patient_email': patient_email}).sort('date', 1)]
    user_reminders = [serialize_doc(rem) for rem in medication_reminders_collection.find({'patient_email': patient_email}).sort('time', 1)]
    user_prescriptions = [serialize_doc(pres) for pres in prescriptions_collection.find({'patient_email': patient_email}).sort('date_prescribed', -1)]

    # --- NEW / CORRECTED: Fetch all registered doctors for the dropdown ---
    # Query the 'users' collection for documents where 'user_type' is 'doctor'
    doctors_from_db = [serialize_doc(doc) for doc in users_collection.find({'user_type': 'doctor'})]

    # Update reminder statuses based on current date (logic from previous iterations)
    today = datetime.now().strftime('%Y-%m-%d')
    for reminder in user_reminders:
        # Check if the reminder's date is in the past and status is pending, mark as missed
        if reminder.get('date') < today and reminder.get('status') == 'Pending':
            medication_reminders_collection.update_one(
                {'_id': ObjectId(reminder['_id'])}, # Query by MongoDB's ObjectId
                {'$set': {'status': 'Missed'}}
            )
            reminder['status'] = 'Missed' # Update local copy for rendering

        # Reset 'taken_today' for a new day if the reminder is daily
        # Check for presence of 'frequency' key to avoid errors for older reminders
        if reminder.get('frequency') == 'daily' and reminder.get('last_checked_date') != today:
             medication_reminders_collection.update_one(
                 {'_id': ObjectId(reminder['_id'])},
                 {'$set': {'taken_today': False, 'status': 'Pending', 'last_checked_date': today}}
             )
             reminder['taken_today'] = False
             reminder['status'] = 'Pending' # Reset to pending for the new day
             reminder['last_checked_date'] = today # Update the last checked date


    return render_template(
        'patient_dashboard.html',
        username=patient_name,
        appointments=user_appointments,
        doctors_data=doctors_from_db, # Pass the dynamically fetched doctors
        medication_reminders=user_reminders, # Corrected variable name to match HTML
        prescriptions=user_prescriptions
    )

@app.route('/doctor_dashboard')
def doctor_dashboard():

    # Displays the doctor dashboard. Fetches appointments relevant to the logged-in doctor
    # and prescriptions issued by this doctor.
    if 'user_email' not in session or session['user_type'] != 'doctor':
        flash('Please log in to access the doctor dashboard.', 'error')
        return redirect(url_for('login')) # Corrected URL endpoint

    doctor_email = session['user_email']
    doctor_name = session['username'] # Use username (which is full name) for display

    # Fetch appointments relevant to this doctor from MongoDB
    # Filter by 'doctor_name' and ensure it's the current logged-in doctor
    doctor_appointments = [serialize_doc(apt) for apt in appointments_collection.find({'doctor_name': doctor_name}).sort('date', 1)]

    # Fetch prescriptions issued by this doctor from MongoDB
    doctor_prescriptions = [serialize_doc(pres) for pres in prescriptions_collection.find({'doctor_name': doctor_name}).sort('date_prescribed', -1)]

    return render_template('doctor_dashboard.html',
                           username=doctor_name, # Pass username for display, which is the doctor's name
                           appointments=doctor_appointments,
                           prescriptions=doctor_prescriptions # Pass the filtered list here
                           )

@app.route('/book_appointment', methods=['POST'])
def book_appointment():
    # Handles booking a new appointment by a patient.Stores appointment details in the 'appointments' collection.
    if 'user_email' not in session or session['user_type'] != 'patient':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login')) # Corrected URL endpoint

    patient_email = session['user_email']
    patient_name = session['username'] # Use full name for patient_name

    doctor_name = request.form['doctor_name'] # Note: Form field name is 'doctor_name'
    appointment_date = request.form['appointment_date']
    appointment_time = request.form['appointment_time']
    reason = request.form['reason']

    new_appointment = {
        'patient_email': patient_email,
        'patient_name': patient_name,
        'doctor_name': doctor_name,
        'date': appointment_date,
        'time': appointment_time,
        'reason': reason,
        'status': 'Pending' # Default status
    }
    appointments_collection.insert_one(new_appointment)

    flash('Appointment booked successfully! Awaiting doctor\'s approval.', 'success')
    return redirect(url_for('patient_dashboard', section='patient-appointments-section'))

@app.route('/cancel_appointment/<appointment_id>')
def cancel_appointment(appointment_id):
    # Handles cancellation of an appointment by a patient. Updates the status in the 'appointments' collection.
    if 'user_email' not in session or session['user_type'] != 'patient':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login')) # Corrected URL endpoint

    patient_email = session['user_email']

    # Find and update appointment in MongoDB by its MongoDB _id and patient email
    appointment = appointments_collection.find_one({'_id': ObjectId(appointment_id), 'patient_email': patient_email})

    if appointment:
        # Allowing cancellation regardless of status as per initial logic, but added a check.
        # Your previous code had: if appointment['status'] == 'Approved': flash('Approved appointments cannot be cancelled by patient.')
        # I'll re-enable allowing cancellation of pending/approved.
        if appointment['status'] not in ['Cancelled', 'Completed']: # Only allow cancellation if not already cancelled or completed
            appointments_collection.update_one(
                {'_id': ObjectId(appointment_id)}, # Query by MongoDB's _id
                {'$set': {'status': 'Cancelled'}}
            )
            flash('Appointment cancelled successfully.', 'success')
        else:
            flash(f"Appointment cannot be cancelled as its current status is '{appointment['status']}'.", 'error')
        return redirect(url_for('patient_dashboard', section='patient-appointments-section'))
    else:
        flash('Appointment not found or you do not have permission to cancel it.', 'error')
        return redirect(url_for('patient_dashboard', section='patient-appointments-section'))


@app.route('/update_appointment_status', methods=['POST'])
def update_appointment_status():
    # Handles status updates for appointments by a doctor.
    if 'user_email' not in session or session['user_type'] != 'doctor':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login')) # Corrected URL endpoint

    appointment_id = request.form['appointment_id'] # This is the string _id from the hidden input
    new_status = request.form['status'] # Form field name is 'status'

    # Update appointment status in MongoDB by its _id
    result = appointments_collection.update_one(
        {'_id': ObjectId(appointment_id)}, # Query by MongoDB's _id
        {'$set': {'status': new_status}}
    )

    if result.matched_count > 0:
        flash(f'Appointment status updated to {new_status}.', 'success')
    else:
        flash('Appointment not found or not updated.', 'error')
    return redirect(url_for('doctor_dashboard', section='doctor-appointments-section'))


@app.route('/add_medication_reminder', methods=['POST'])
def add_medication_reminder():
    # Handles adding new medication reminders for patients.
    if 'user_email' not in session or session['user_type'] != 'patient':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login')) # Corrected URL endpoint

    patient_email = session['user_email']
    medication = request.form['medication']
    dosage = request.form['dosage']
    frequency = request.form['frequency']
    time = request.form['time']
    start_date_str = request.form['start_date']

    try:
        datetime.strptime(start_date_str, '%Y-%m-%d').date()
    except ValueError:
        flash('Invalid date format for reminder. Please use YYYY-MM-DD.', 'error')
        return redirect(url_for('patient_dashboard', section='patient-medication-reminders-section'))

    new_reminder = {
        'patient_email': patient_email,
        'medication': medication,
        'dosage': dosage,
        'frequency': frequency,
        'time': time,
        'date': start_date_str, # Store as string
        'status': 'Upcoming', # Initial status
        'taken_today': False, # To track if taken for the current day
        'last_checked_date': datetime.now().strftime('%Y-%m-%d') # To manage daily resets
    }

    medication_reminders_collection.insert_one(new_reminder)

    flash(f'Medication reminder for {medication} added successfully!', 'success')
    return redirect(url_for('patient_dashboard', section='patient-medication-reminders-section'))


@app.route('/mark_reminder_taken/<reminder_id>', methods=['POST'])
def mark_reminder_taken(reminder_id):
    # Handles marking medication reminders as taken or pending.
    if 'user_email' not in session or session['user_type'] != 'patient':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login')) # Corrected URL endpoint

    patient_email = session['user_email']
    action = request.form.get('action') # 'take' or 'unmark' from the forms

    # Find the reminder by its _id and patient email
    reminder = medication_reminders_collection.find_one({'_id': ObjectId(reminder_id), 'patient_email': patient_email})

    if reminder:
        today = datetime.now().strftime('%Y-%m-%d')

        # Initialize 'last_checked_date' if it doesn't exist (for old data)
        if 'last_checked_date' not in reminder:
            medication_reminders_collection.update_one(
                {'_id': ObjectId(reminder_id)},
                {'$set': {'last_checked_date': today}}
            )
            reminder['last_checked_date'] = today

        # Daily reset logic for reminders
        if reminder.get('frequency') == 'daily' and reminder['last_checked_date'] != today:
            medication_reminders_collection.update_one(
                {'_id': ObjectId(reminder_id)},
                {'$set': {'taken_today': False, 'status': 'Pending', 'last_checked_date': today}}
            )
            reminder['taken_today'] = False
            reminder['status'] = 'Pending'
            reminder['last_checked_date'] = today

        # Apply the action
        if action == 'take':
            if not reminder['taken_today']: # Only mark if not already taken today
                medication_reminders_collection.update_one(
                    {'_id': ObjectId(reminder_id)},
                    {'$set': {'taken_today': True, 'status': 'Taken'}}
                )
                flash(f"Medication '{reminder['medication']}' marked as taken for today.", 'success')
            else:
                flash(f"Medication '{reminder['medication']}' already marked as taken today.", 'info')
        elif action == 'unmark':
            if reminder['taken_today']: # Only unmark if currently taken
                medication_reminders_collection.update_one(
                    {'_id': ObjectId(reminder_id)},
                    {'$set': {'taken_today': False, 'status': 'Pending'}}
                )
                flash(f"Medication '{reminder['medication']}' unmarked for today.", 'info')
            else:
                flash(f"Medication '{reminder['medication']}' is already pending.", 'info')
        return redirect(url_for('patient_dashboard', section='patient-medication-reminders-section'))
    else:
        flash('Reminder not found.', 'error')
        return redirect(url_for('patient_dashboard', section='patient-medication-reminders-section'))


@app.route('/issue_prescription', methods=['POST'])
def issue_prescription():
    # Handles issuing new prescriptions by a doctor.
    if 'user_email' not in session or session['user_type'] != 'doctor':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login')) # Corrected URL endpoint

    doctor_name = session['username']
    patient_email = request.form['patient_email_prescribe']
    medication = request.form['medication']
    dosage = request.form['dosage']
    instructions = request.form['instructions']

    # Find the patient in the users collection to get their name
    patient_user = users_collection.find_one({'email': patient_email, 'user_type': 'patient'})
    if not patient_user:
        flash('Patient with this email does not exist or is not a patient user type.', 'error')
        return redirect(url_for('doctor_dashboard', section='doctor-issue-prescription-section'))

    new_prescription = {
        'doctor_name': doctor_name,
        'patient_email': patient_email,
        'patient_name': patient_user['name'], # Get actual patient name from DB
        'medication': medication,
        'dosage': dosage,
        'instructions': instructions,
        'date_prescribed': datetime.now().strftime('%Y-%m-%d')
    }

    prescriptions_collection.insert_one(new_prescription)

    flash(f'Prescription for {patient_user["name"]} issued successfully!', 'success')
    return redirect(url_for('doctor_dashboard', section='doctor-prescriptions-section'))

@app.route('/delete_reminder/<reminder_id>')
def delete_reminder(reminder_id):
    #Handles deleting a medication reminder by a patient.
    if 'user_email' not in session or session['user_type'] != 'patient':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login')) # Corrected URL endpoint

    patient_email = session['user_email']
    # Delete reminder from MongoDB by its _id and patient email
    result = medication_reminders_collection.delete_one({'_id': ObjectId(reminder_id), 'patient_email': patient_email})

    if result.deleted_count > 0:
        flash('Medication reminder deleted successfully.', 'success')
    else:
        flash('Medication reminder not found.', 'error')
    return redirect(url_for('patient_dashboard', section='patient-medication-reminders-section'))

if __name__ == '__main__':
    app.run(debug=True)