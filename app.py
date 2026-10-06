from flask import Flask, render_template, redirect, url_for
import mysql.connector
import boto3
import json
from config import DB_CONFIG

app = Flask(__name__)
lambda_client = boto3.client(
    "lambda",
    region_name="ap-southeast-2"
)

LAMBDA_FUNCTION_NAME = "skill-matching-function"

# ==========================================
# AWS RDS DATABASE CONNECTION
# ==========================================
def get_db_connection():
    return mysql.connector.connect(**DB_CONFIG)


# ==========================================
# DASHBOARD
# ==========================================
@app.route("/")
def dashboard():

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    cursor.execute("SELECT COUNT(*) AS total FROM employees")
    employee_count = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) AS total FROM tasks")
    task_count = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) AS total FROM allocations")
    allocation_count = cursor.fetchone()["total"]

    cursor.close()
    connection.close()

    return render_template(
        "index.html",
        employee_count=employee_count,
        task_count=task_count,
        allocation_count=allocation_count
    )


# ==========================================
# EMPLOYEES
# ==========================================
@app.route("/employees")
def employees():

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
        SELECT
            employee_id,
            employee_code,
            name,
            experience_years,
            workload_percentage,
            availability
        FROM employees
        ORDER BY employee_id
    """)

    employee_list = cursor.fetchall()

    cursor.close()
    connection.close()

    return render_template(
        "employees.html",
        employees=employee_list
    )


# ==========================================
# TASKS
# ==========================================
@app.route("/tasks")
def tasks():

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
        SELECT
            task_id,
            task_code,
            task_name,
            priority,
            estimated_hours,
            status
        FROM tasks
        ORDER BY task_id
    """)

    task_list = cursor.fetchall()

    cursor.close()
    connection.close()

    return render_template(
        "tasks.html",
        tasks=task_list
    )


# ==========================================
# SKILL MISMATCH DETECTION
# ==========================================
@app.route("/matching")
def matching():

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
        SELECT
            e.employee_code,
            e.name,
            t.task_code,
            t.task_name,

            COUNT(ts.skill_id) AS required_skills,

            COUNT(
                CASE
                    WHEN es.skill_id IS NOT NULL
                    THEN 1
                END
            ) AS matched_skills

        FROM employees e

        CROSS JOIN tasks t

        JOIN task_skills ts
            ON ts.task_id = t.task_id

        LEFT JOIN employee_skills es
            ON es.employee_id = e.employee_id
            AND es.skill_id = ts.skill_id

        GROUP BY
            e.employee_id,
            e.employee_code,
            e.name,
            t.task_id,
            t.task_code,
            t.task_name

        ORDER BY
            t.task_id,
            matched_skills DESC
    """)

    matches = cursor.fetchall()

    for match in matches:

        if match["required_skills"] > 0:

            match["match_score"] = round(
                (
                    match["matched_skills"]
                    / match["required_skills"]
                ) * 100,
                2
            )

        else:

            match["match_score"] = 0

        if match["match_score"] == 100:

            match["status"] = "Full Match"

        elif match["match_score"] > 0:

            match["status"] = "Partial Match"

        else:

            match["status"] = "Skill Mismatch"

    cursor.close()
    connection.close()

    return render_template(
        "matching.html",
        matches=matches
    )


# ==========================================
# GET RECOMMENDATIONS
# ==========================================
def get_recommendations():

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
        SELECT
            e.employee_id,
            e.employee_code,
            e.name,
            e.experience_years,
            e.workload_percentage,
            e.availability,

            t.task_id,
            t.task_code,
            t.task_name,

            COUNT(ts.skill_id) AS required_skills,

            COUNT(
                CASE
                    WHEN es.skill_id IS NOT NULL
                    THEN 1
                END
            ) AS matched_skills

        FROM employees e

        CROSS JOIN tasks t

        JOIN task_skills ts
            ON ts.task_id = t.task_id

        LEFT JOIN employee_skills es
            ON es.employee_id = e.employee_id
            AND es.skill_id = ts.skill_id

        GROUP BY
            e.employee_id,
            e.employee_code,
            e.name,
            e.experience_years,
            e.workload_percentage,
            e.availability,
            t.task_id,
            t.task_code,
            t.task_name

        ORDER BY
            t.task_id,
            matched_skills DESC
    """)

    recommendations = cursor.fetchall()

    cursor.close()
    connection.close()


    # ==========================================
    # CALCULATE WEIGHTED SCORES
    # ==========================================

    for employee in recommendations:

        # Skill Match = 70%

        if employee["required_skills"] > 0:

            skill_match = (
                employee["matched_skills"]
                / employee["required_skills"]
            ) * 100

        else:

            skill_match = 0


        # Experience = 20%

        experience_score = min(
            float(employee["experience_years"]) * 20,
            100
        )


        # Availability = 10%

        if employee["availability"] == "Available":

            availability_score = 100

        else:

            availability_score = 0


        # Final weighted score

        final_score = (
            (skill_match * 0.70)
            + (experience_score * 0.20)
            + (availability_score * 0.10)
        )


        employee["skill_match"] = round(
            skill_match,
            2
        )

        employee["experience_score"] = round(
            experience_score,
            2
        )

        employee["availability_score"] = (
            availability_score
        )

        employee["final_score"] = round(
            final_score,
            2
        )


    # ==========================================
    # SELECT BEST EMPLOYEE FOR EACH TASK
    # ==========================================

    best_employees = {}

    for employee in recommendations:

        task_id = employee["task_id"]

        if task_id not in best_employees:

            best_employees[task_id] = employee

        else:

            if (
                employee["final_score"]
                > best_employees[task_id]["final_score"]
            ):

                best_employees[task_id] = employee


    best_recommendations = list(
        best_employees.values()
    )

    best_recommendations.sort(
        key=lambda x: x["task_id"]
    )

    return best_recommendations


# ==========================================
# EMPLOYEE RECOMMENDATION
# ==========================================
@app.route("/recommendation")
def recommendation():

    recommendations = get_recommendations()

    return render_template(
        "recommendation.html",
        recommendations=recommendations
    )


# ==========================================
# ALLOCATE RECOMMENDED EMPLOYEES
# ==========================================
@app.route("/allocate", methods=["POST"])
def allocate():

    recommendations = get_recommendations()

    connection = get_db_connection()
    cursor = connection.cursor()

    for recommendation in recommendations:

        task_id = recommendation["task_id"]
        employee_id = recommendation["employee_id"]
        match_score = recommendation["skill_match"]

        # Check whether this task is already allocated

        cursor.execute("""
            SELECT allocation_id
            FROM allocations
            WHERE task_id = %s
        """, (task_id,))

        existing = cursor.fetchone()

        # Only insert if task is not already allocated

        if existing is None:

            cursor.execute("""
                INSERT INTO allocations
                (
                    task_id,
                    employee_id,
                    match_score,
                    missing_skills,
                    status
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
            """, (
                task_id,
                employee_id,
                match_score,
                "None",
                "Allocated"
            ))


    connection.commit()

    cursor.close()
    connection.close()

    return redirect(url_for("allocations"))


# ==========================================
# TASK ALLOCATION PAGE
# ==========================================
@app.route("/allocations")
def allocations():

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
        SELECT
            a.allocation_id,
            t.task_code,
            t.task_name,
            e.employee_code,
            e.name,
            a.match_score,
            a.missing_skills,
            a.status,
            a.allocated_at

        FROM allocations a

        JOIN tasks t
            ON a.task_id = t.task_id

        JOIN employees e
            ON a.employee_id = e.employee_id

        ORDER BY a.allocation_id DESC
    """)

    allocation_list = cursor.fetchall()

    cursor.close()
    connection.close()

    return render_template(
        "allocations.html",
        allocations=allocation_list
    )


# ==========================================
# RUN FLASK APPLICATION
# ==========================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

# AWS Lambda connection
lambda_client = boto3.client(
    "lambda",
    region_name="ap-southeast-2"
)

LAMBDA_FUNCTION_NAME = "skill-matching-function"

def invoke_lambda(employees, tasks):
    payload = {
        "employees": employees,
        "tasks": tasks
    }

    response = lambda_client.invoke(
        FunctionName=LAMBDA_FUNCTION_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload)
    )

    result = json.loads(
        response["Payload"].read().decode("utf-8")
    )

    return result
