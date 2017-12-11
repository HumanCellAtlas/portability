import os
import subprocess
import uuid

from flask import Flask
from flask_restful import Resource, Api, abort, reqparse

import werkzeug

# Set up the flask app and API
app = Flask(__name__)
api = Api(app)

# Declare two global variables that will hold state.
WORKFLOW_TESTS = {}
ENVIRONMENTS = {}

# Functions for running the test
def submit_test(environment_image, workflow_source, workflow_inputs, workflow_dependencies):

    docker_cmd = ["docker", "run",
                  "-v", "/io:/io",
                  "-v", "/var/run/docker.sock:/var/run/docker.sock",
                  environment_image,
                  "--workflowSource", workflow_source,
                  "--workflowInputs", workflow_inputs]

    if workflow_dependencies:
        docker_cmd.extend(["--workflowDependencies", workflow_dependencies])

    proc = subprocess.Popen(docker_cmd)
    proc.communicate()


    if proc.returncode == 0:
        workflow_test = {"status": "Succeeded"}
    else:
        workflow_test = {"status": "Failed"}
    workflow_test["command"] = ' '.join(docker_cmd)

    return workflow_test

class WorkflowTest(Resource):
    def get(self):
        parser = reqparse.RequestParser()
        parser.add_argument("workflowTestId")
        args = parser.parse_args()
        try:
            return WORKFLOW_TESTS[args.WorkflowTestId]
        except KeyError:
            abort(404, message="Workflow Test Id {} not found". format(args.WorkflowTestId))

    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument("workflowSource")
        parser.add_argument("workflowInputs")
        parser.add_argument("workflowDependencies",
                            type=werkzeug.datastructures.FileStorage,
                            location='files')
        args = parser.parse_args()

        # Create a unique id for this workflow test
        workflow_test_id = str(uuid.uuid4())

        # Store the submitted WDLs and inputs.
        os.makedirs(os.path.join(os.sep, 'io', workflow_test_id))
        wdl_path = os.path.join(os.sep, "io", workflow_test_id, "source.wdl")
        inputs_path = os.path.join(os.sep, "io", workflow_test_id, "inputs.json")
        deps_path = os.path.join(os.sep, "io", workflow_test_id, "dependencies.zip")

        with open(wdl_path, 'w') as wdl:
            wdl.write(args.workflowSource)
        with open(inputs_path, 'w') as inputs:
            inputs.write(args.workflowInputs)
        args.workflowDependencies.save(deps_path)

        test = {"id": workflow_test_id,
                "environments": []}

        # Iterate over the registered environments, running the tests
        for environment in ENVIRONMENTS.values():
            env_test_info = submit_test(environment["image"], wdl_path,
                                        inputs_path, deps_path)
            env_test_info["name"] = environment["name"]

            test["environments"].append(env_test_info)

        WORKFLOW_TESTS[workflow_test_id] = test

        return test, 201

class Environment(Resource):

    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument("environmentName")
        parser.add_argument("environmentImage")
        args = parser.parse_args()

        environment = {"name": args.environmentName, "image": args.environmentImage}
        ENVIRONMENTS[args.environmentName] = environment

        return environment, 201

api.add_resource(WorkflowTest, '/workflow_test')
api.add_resource(Environment, '/environment')


if __name__ == '__main__':
    app.run(debug=True)
