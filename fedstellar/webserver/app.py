import datetime
import hashlib
import json
import os
import signal
import subprocess
import sys

from ansi2html import Ansi2HTMLConverter

from fedstellar.controller import Controller

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, session, url_for, redirect, render_template, request, abort, flash, send_file, make_response, jsonify, Response
from werkzeug.utils import secure_filename
from fedstellar.webserver.database import list_users, verify, delete_user_from_db, add_user, scenario_update_record, scenario_set_all_status_to_finished, get_active_scenario, remove_all_nodes, get_scenario_by_name, get_user_info
from fedstellar.webserver.database import read_note_from_db, write_note_into_db, delete_note_from_db, match_user_id_with_note_id
from fedstellar.webserver.database import image_upload_record, list_images_for_user, match_user_id_with_image_uid, delete_image_from_db, get_image_file_name, update_node_record, list_nodes

app = Flask(__name__)
app.config.from_object('config')
app.config['log_dir'] = os.environ.get('FEDSTELLAR_LOGS_DIR')
app.config['config_dir'] = os.environ.get('FEDSTELLAR_CONFIG_DIR')
app.config['python_path'] = os.environ.get('FEDSTELLAR_PYTHON_PATH')


# Detect CTRL+C from parent process
def signal_handler(signal, frame):
    print('You pressed Ctrl+C [webserver]!')
    scenario_set_all_status_to_finished()
    remove_all_nodes()
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)


@app.errorhandler(401)
def fedstellar_401(error):
    return render_template("401.html"), 401


@app.errorhandler(403)
def fedstellar_403(error):
    return render_template("403.html"), 403


@app.errorhandler(404)
def fedstellar_404(error):
    return render_template("404.html"), 404


@app.errorhandler(405)
def fedstellar_405(error):
    return render_template("405.html"), 405


@app.errorhandler(413)
def fedstellar_413(error):
    return render_template("413.html"), 413


@app.route("/")
def fedstellar_home():
    return render_template("index.html")


@app.route("/monitoring/<scenario_name>/private/")
def fedstellar_private(scenario_name):
    if "user" in session.keys():
        notes_list = read_note_from_db(session['user'])
        notes_table = zip([x[0] for x in notes_list],
                          [x[1] for x in notes_list],
                          [x[2] for x in notes_list],
                          ["/delete_note/" + x[0] for x in notes_list])

        images_list = list_images_for_user(session['user'])
        images_table = zip([x[0] for x in images_list],
                           [x[1] for x in images_list],
                           [x[2] for x in images_list],
                           ["/delete_image/" + x[0] for x in images_list],
                           ["/images/" + x[0] for x in images_list])

        return render_template("private.html", scenario_name=scenario_name, notes=notes_table, images=images_table)
    else:
        return abort(401)


@app.route("/admin/")
def fedstellar_admin():
    if session.get("role", None) == "admin":
        user_list = list_users(all_info=True)
        user_names = [x[0] for x in user_list]
        user_roles = [x[2] for x in user_list]
        user_table = zip(range(1, len(user_list) + 1),
                         user_names,
                         user_roles,
                         [x + y for x, y in zip(["/delete_user/"] * len(user_names), user_names)])
        return render_template("admin.html", users=user_table)
    else:
        return abort(401)


@app.route("/write_note", methods=["POST"])
def fedstellar_write_note():
    text_to_write = request.form.get("text_note_to_take")
    write_note_into_db(session['user'], text_to_write)

    return (redirect(url_for("fedstellar_private")))


@app.route("/delete_note/<note_id>", methods=["GET"])
def fedstellar_delete_note(note_id):
    if session.get("user", None) == match_user_id_with_note_id(note_id):  # Ensure the current user is NOT operating on other users' note.
        delete_note_from_db(note_id)
    else:
        return abort(401)
    return (redirect(url_for("fedstellar_private")))


# Reference: http://flask.pocoo.org/docs/0.12/patterns/fileuploads/
ALLOWED_EXTENSIONS = set(['png', 'jpg', 'jpeg', 'gif'])


def allowed_file(filename):
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route("/upload_image", methods=['POST'])
def fedstellar_upload_image():
    if request.method == 'POST':
        # check if the post request has the file part
        if 'file' not in request.files:
            flash('No file part', category='danger')
            return (redirect(url_for("fedstellar_private")))
        file = request.files['file']
        # if user does not select file, browser also submit a empty part without filename
        if file.filename == '':
            flash('No selected file', category='danger')
            return (redirect(url_for("fedstellar_private")))
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            upload_time = str(datetime.datetime.now())
            image_uid = hashlib.sha1((upload_time + filename).encode()).hexdigest()
            # Save the image into UPLOAD_FOLDER
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], image_uid + "-" + filename))
            # Record this uploading in database
            image_upload_record(image_uid, session['user'], filename, upload_time)
            return (redirect(url_for("fedstellar_private")))

    return (redirect(url_for("fedstellar_private")))


@app.route("/images/<image_uid>/", methods=['GET'])
def fedstellar_get_image(image_uid):
    if session.get("user", None) == match_user_id_with_image_uid(image_uid):  # Ensure the current user is NOT operating on other users' note.
        # Return the image to the browser
        image_file_name = get_image_file_name(image_uid)
        print("Sending image: " + image_file_name)
        return send_from_directory(app.config['UPLOAD_FOLDER'], image_file_name)
    else:
        return abort(401)


def send_from_directory(directory, filename, **options):
    """Sends a file from a given directory with :func:`send_file`.

    :param directory: the directory to look for the file in.
    :param filename: the name of the file to send.
    :param options: the options to forward to :func:`send_file`.
    """
    return send_file(os.path.join(directory, filename), **options)


@app.route("/delete_image/<image_uid>", methods=["GET"])
def fedstellar_delete_image(image_uid):
    if session.get("user", None) == match_user_id_with_image_uid(image_uid):  # Ensure the current user is NOT operating on other users' note.
        # delete the corresponding record in database
        delete_image_from_db(image_uid)
        # delete the corresponding image file from image pool
        image_to_delete_from_pool = [y for y in [x for x in os.listdir(app.config['UPLOAD_FOLDER'])] if y.split("-", 1)[0] == image_uid][0]
        os.remove(os.path.join(app.config['UPLOAD_FOLDER'], image_to_delete_from_pool))
    else:
        return abort(401)
    return (redirect(url_for("fedstellar_private")))


@app.route("/login", methods=["POST"])
def fedstellar_login():
    user_submitted = request.form.get("user").upper()
    if (user_submitted in list_users()) and verify(user_submitted, request.form.get("password")):
        user_info = get_user_info(user_submitted)
        session['user'] = user_submitted
        session['role'] = user_info[2]
        return "Login successful", 200
    else:
        # flash(u'Invalid password provided', 'error')
        abort(401)


@app.route("/logout/")
def fedstellar_logout():
    session.pop("user", None)
    return (redirect(url_for("fedstellar_home")))


@app.route("/delete_user/<user>/", methods=['GET'])
def fedstellar_delete_user(user):
    if session.get("role", None) == "admin":
        if user == "ADMIN":  # ADMIN account can't be deleted.
            return abort(403)
        if user == session['user']:  # Current user can't delete himself.
            return abort(403)

        # [1] Delete this user's images in image pool
        images_to_remove = [x[0] for x in list_images_for_user(user)]
        for f in images_to_remove:
            image_to_delete_from_pool = [y for y in [x for x in os.listdir(app.config['UPLOAD_FOLDER'])] if y.split("-", 1)[0] == f][0]
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], image_to_delete_from_pool))
        # [2] Delete the records in database files
        delete_user_from_db(user)
        return (redirect(url_for("fedstellar_admin")))
    else:
        return abort(401)


@app.route("/add_user", methods=["POST"])
def fedstellar_add_user():
    if session.get("role", None) == "admin":  # only Admin should be able to add user.
        # before we add the user, we need to ensure this is doesn't exsit in database. We also need to ensure the id is valid.
        if request.form.get('user').upper() in list_users():
            user_list = list_users()
            user_table = zip(range(1, len(user_list) + 1),
                             user_list,
                             [x + y for x, y in zip(["/delete_user/"] * len(user_list), user_list)])
            return render_template("admin.html", id_to_add_is_duplicated=True, users=user_table)
        if " " in request.form.get('user') or "'" in request.form.get('user') or '"' in request.form.get('user'):
            user_list = list_users()
            user_table = zip(range(1, len(user_list) + 1),
                             user_list,
                             [x + y for x, y in zip(["/delete_user/"] * len(user_list), user_list)])
            return render_template("admin.html", id_to_add_is_invalid=True, users=user_table)
        else:
            add_user(request.form.get('user'), request.form.get('password'), request.form.get('role'))
            return (redirect(url_for("fedstellar_admin")))
    else:
        return abort(401)


#                                                   #
# ------------------- Monitoring ------------------ #
#                                                   #

@app.route("/api/monitoring", methods=["GET"])
@app.route("/monitoring", methods=["GET"])
def fedstellar_monitoring():
    if "user" in session.keys():
        scenario_running = get_active_scenario()
        if scenario_running:
            scenario_name = scenario_running[0]
            scenario_start_time = scenario_running[1]
            scenario_end_time = scenario_running[2]
            scenario_description = scenario_running[3]
            scenario_status = scenario_running[4]

            nodes_list = list_nodes()
            # Get json data from each node configuration file
            nodes_config = []
            # Generate an array with True for each node that is running
            nodes_status = []
            nodes_offline = []
            for i, node in enumerate(nodes_list):
                with open(os.path.join(app.config['config_dir'], f'participant_{node[1]}.json')) as f:
                    nodes_config.append(json.load(f))
                if datetime.datetime.now() - datetime.datetime.strptime(node[8], "%Y-%m-%d %H:%M:%S.%f") > datetime.timedelta(seconds=20):
                    nodes_status.append(False)
                    nodes_offline.append(node[2] + ':' + str(node[3]))
                else:
                    nodes_status.append(True)

            nodes_table = zip([x[0] for x in nodes_list],  # UID
                              [x[1] for x in nodes_list],  # IDX
                              [x[2] for x in nodes_list],  # IP
                              [x[3] for x in nodes_list],  # Port
                              [x[4] for x in nodes_list],  # Role
                              [x[5] for x in nodes_list],  # Neighbors
                              [x[6] for x in nodes_list],  # Latitude
                              [x[7] for x in nodes_list],  # Longitude
                              [x[8] for x in nodes_list],  # Timestamp
                              [x[9] for x in nodes_list],  # Federation
                              [x[10] for x in nodes_list],  # Scenario name
                              nodes_status  # Status
                              )

            # Nodes_config and nodes_list are lists which contain the nodes that are online
            for node in nodes_config:
                node_config = node['network_args']['ip'] + ':' + str(node['network_args']['port'])
                if node_config in nodes_offline:
                    nodes_config.remove(node)
            for node in nodes_list:
                node_list = node[2] + ':' + str(node[3])
                if node_list in nodes_offline:
                    nodes_list.remove(node)

            if os.path.exists(os.path.join(app.config['config_dir'], 'topology.png')):
                if os.path.getmtime(os.path.join(app.config['config_dir'], 'topology.png')) < max([os.path.getmtime(os.path.join(app.config['config_dir'], f'participant_{node[1]}.json')) for node in nodes_list]):
                    # Update the 3D topology and image
                    update_topology(scenario_name, nodes_list, nodes_config)
            else:
                update_topology(scenario_name, nodes_list, nodes_config)

            if request.path == "/monitoring":
                return render_template("monitoring.html", nodes=nodes_table, scenario_name=scenario_name, scenario_description=scenario_description)
            elif request.path == "/api/monitoring":
                return jsonify({'scenario_status': scenario_status, 'nodes_table': list(nodes_table), 'scenario_name': scenario_name, 'scenario_description': scenario_description}), 200
            else:
                return abort(401)

        else:
            if request.path == "/monitoring":
                return render_template("monitoring.html")
            elif request.path == "/api/monitoring":
                return jsonify({'scenario_status': 'offline'}), 200
            else:
                return abort(401)


def update_topology(scenario_name, nodes_list, nodes_config):
    print("Updating topology (3D and image)... Num. nodes: " + str(len(nodes_config)))
    import numpy as np
    nodes = []
    for node in nodes_list:
        nodes.append(node[2] + ':' + str(node[3]))
    matrix = np.zeros((len(nodes), len(nodes)))
    for node in nodes_list:
        for neighbour in node[5].split(" "):
            if neighbour != '':
                matrix[nodes.index(node[2] + ':' + str(node[3])), nodes.index(neighbour)] = 1
    from fedstellar.utils.topologymanager import TopologyManager
    tm = TopologyManager(n_nodes=len(nodes_list), topology=matrix, scenario_name=scenario_name)
    tm.update_nodes(nodes_config)
    tm.draw_graph(path=os.path.join(app.config['config_dir'], f'topology.png'))  # TODO: Improve this

    # tm.update_topology_3d_json(participants=nodes_config, path=os.path.join(app.config['config_dir'], f'topology.json'))


@app.route("/monitoring/update/<uid>", methods=['POST'])
def fedstellar_update_node(uid):
    if request.method == 'POST':
        # Check if the post request is a json, if not, return 400
        if request.is_json:
            config = request.get_json()
            timestamp = datetime.datetime.now()
            # Update file in the local directory
            with open(os.path.join(app.config['config_dir'], f'participant_{config["device_args"]["idx"]}.json'), "w") as f:
                json.dump(config, f, sort_keys=False, indent=2)

            # Update the node in database
            update_node_record(str(config['device_args']['uid']), str(config['device_args']['idx']), str(config['network_args']['ip']), str(config['network_args']['port']), str(config['device_args']['role']), str(config['network_args']['neighbors']), str(config['geo_args']['latitude']),
                               str(config['geo_args']['longitude']),
                               str(timestamp), str(config['scenario_args']['federation']), str(config['scenario_args']['name']))

            return make_response("Node updated successfully", 200)
        else:
            return abort(400)


@app.route("/monitoring/update/<uid>/logs", methods=['POST'])
def fedstellar_update_node_logs(uid):
    if request.method == 'POST':
        # Get the logs from the request (is not json)
        logs = request.data.decode('utf-8')
        # Update log file
        with open(os.path.join(app.config['LOG_FOLDER_WEBSERVER'], f'{uid}.log'), "a") as f:
            f.write(logs)

        return make_response("Logs received successfully", 200)


@app.route("/monitoring/<uid>/logs", methods=["GET"])  # TODO: maybe change scenario name and save directly in config folder
def fedstellar_monitoring_log(uid):
    if "user" in session.keys():
        logs = os.path.join(app.config['LOG_FOLDER_WEBSERVER'], f'{uid}.log')
        if os.path.exists(logs):
            return send_file(logs, mimetype='text/plain', as_attachment=True)
        else:
            abort(404)
    else:
        make_response("You are not authorized to access this page.", 401)


@app.route("/monitoring/<uid>/logs/debug", methods=["GET"])  # TODO: maybe change scenario name and save directly in config folder
def fedstellar_monitoring_log_debug(uid):
    if "user" in session.keys():
        logs = os.path.join(app.config['LOG_FOLDER_WEBSERVER'], f'{uid}_debug.log')
        if os.path.exists(logs):
            return send_file(logs, mimetype='text/plain', as_attachment=True)
        else:
            abort(404)
    else:
        make_response("You are not authorized to access this page.", 401)


@app.route("/monitoring/<uid>/logs/<number>", methods=["GET"])
def fedstellar_monitoring_log_x(uid, number):
    if "user" in session.keys():
        # Send file (is not a json file) with the log
        logs = os.path.join(app.config['LOG_FOLDER_WEBSERVER'], f'{uid}.log')
        if os.path.exists(logs):
            # Open file mantaining the file format (for example, new lines)
            with open(logs, 'r') as f:
                # Read the last n lines of the file
                lines = f.readlines()[-int(number):]
                # Join the lines in a single string
                lines = ''.join(lines)
                # Convert the ANSI escape codes to HTML
                converter = Ansi2HTMLConverter()
                html_text = converter.convert(lines, full=False)
                # Return the string
                return Response(html_text, mimetype='text/plain')
        else:
            return Response("No logs available", mimetype='text/plain')

    else:
        make_response("You are not authorized to access this page.", 401)


# @app.route("/monitoring/<scenario_name>/topology/3d", methods=["GET"])
# def fedstellar_monitoring_3d(scenario_name):
#     if "user" in session.keys():
#         topology3d = os.path.join(app.config['config_dir'], f'topology.json')
#         return send_file(topology3d, mimetype='application/json')
#     else:
#         make_response("You are not authorized to access this page.", 401)


@app.route("/monitoring/<scenario_name>/topology/image/", methods=["GET"])  # TODO: maybe change scenario name and save directly in config folder
def fedstellar_monitoring_image(scenario_name):
    if "user" in session.keys():
        topology_image = os.path.join(app.config['config_dir'], f'topology.png')
        if os.path.exists(topology_image):
            return send_file(topology_image, mimetype='image/png')
        else:
            abort(404)
    else:
        make_response("You are not authorized to access this page.", 401)

@app.route("/monitoring/<scenario_name>/stop", methods=["GET"])
def fedstellar_monitoring_stop_scenario(scenario_name):
    # Stop the scenario
    if "user" in session.keys():
        from fedstellar.controller import Controller
        os.system("""osascript -e 'tell application "Terminal" to quit'""") if sys.platform == "darwin" else None
        nodes = list_nodes()
        for node in nodes:
            # Kill the node
            Controller.killport(node[3])

        scenario_set_all_status_to_finished()
        Controller.remove_config_files()
        remove_all_nodes()

        return redirect(url_for('fedstellar_home'))
    else:
        pass


#                                                   #
# ------------------- Deployment ------------------ #
#                                                   #


@app.route("/deployment/", methods=["GET"])
def fedstellar_private_scenario():
    if "user" in session.keys():
        scenario_running = get_active_scenario()
        if not scenario_running:
            return render_template("deployment.html")
        else:
            return render_template("deployment.html", scenario_name=scenario_running[0])
    else:
        return abort(401)


@app.route("/deployment/participant/file", methods=["GET"])
def fedstellar_monitoring_participant_file():
    if "user" in session.keys():
        participant_file_example = os.path.join(app.config['config_dir'], f'participant.json.example')
        return send_file(participant_file_example, mimetype='application/json')
    else:
        make_response("You are not authorized to access this page.", 401)


@app.route("/deployment/run", methods=["POST"])
# TODO: Improve the display of the configurations and the customization of the scenario
def fedstellar_private_scenario_run():
    if "user" in session.keys():
        # Receive a JSON data with the scenario configuration
        if request.is_json:
            data = request.get_json()

            nodes = data['nodes']

            args = {
                "config": app.config['config_dir'],
                "logs": app.config['log_dir'],
                "n_nodes": data["n_nodes"],
                "matrix": data["matrix"],
                "federation": data["federation"],
                "topology": data["topology"],
                "simulation": data["simulation"],
                "env": None,
                "webserver": True,
                "python": app.config['python_path'],
            }
            # For each node, create a new file in config directory
            import shutil
            # Loop dictionary of nodes
            for node in nodes:
                node_config = nodes[node]
                # Create a copy of participant.json.example and update the file with the update values
                participant_file = os.path.join(app.config['config_dir'], f'participant_{node_config["id"]}.json')
                # Create a copy of participant.json.example
                shutil.copy(os.path.join(app.config['CONFIG_FOLDER_WEBSERVER'], f'participant.json.example'), participant_file)
                # Update IP, port, and role
                with open(participant_file) as f:
                    participant_config = json.load(f)
                participant_config['network_args']['ip'] = node_config["ip"]
                participant_config['network_args']['ipdemo'] = node_config["ipdemo"]  # legacy code
                participant_config['network_args']['port'] = int(node_config["port"])
                # participant_config['device_args']['idx'] = i
                participant_config["device_args"]["start"] = node_config["start"]
                participant_config["device_args"]["role"] = node_config["role"]

                with open(participant_file, 'w') as f:
                    json.dump(participant_config, f, sort_keys=False, indent=2)

            # Create a argparse object
            import argparse
            args = argparse.Namespace(**args)
            controller = Controller(args)  # Generate an instance of controller in this new process
            controller.load_configurations_and_start_nodes()
            # Generate/Update the scenario in the database
            scenario_update_record(scenario_name=controller.scenario_name, start_time=controller.start_date_scenario, end_time="", status="running", description=data["scenario_description"])

            return redirect(url_for("fedstellar_monitoring"))
        else:
            return abort(401)
    else:
        return abort(401)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)