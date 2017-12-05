#!/usr/bin/env python3
"""A script to run a WDL (and its subWDLs) in a local cromwell environment.

This is placed within a docker container that has cromwell and tools for
localizing inputs installed. It's run as the entrypoint to the container.

There is one significant and very sad constraint on how this script can be
run. This is being run in a docker container, and the WDL it executes can
specify docker images in which to run tasks. So we've got a
docker-within-docker situation. And, the docker socket available to this
script and to cromwell is mounted from the calling environment, so you
have to make sure any paths you try to mount are valid paths in the calling
environment, not within this container.
"""

import argparse
import json
import os
import subprocess
import sys

# Cromwell is already in the image.
CROWMELL_PATH = os.path.join(os.sep, "cromwell-29.jar")

def localize_inputs(inputs_json, io_dir):
    """Some inputs to the WDL are files. We need to download them so they're local
    to the instance of cromwell we're running.

    Args:
      inputs_json: Local path to the JSON file that would normally get passed to
        cromwell with a '-i'.

    Returns:
      localized_inputs_dict: Dictionary where the keys in the original JSON that
        referred to remote files have replaces with local paths.
    """

    inputs_dict = json.load(open(inputs_json))

    localized_inputs_dict = {}

    for entry, value in inputs_dict.items():
        # TODO: There are at least three protocols to support here: http, s3
        # and gs.
        if value.startswith("https://") or value.startswith("http://"):
            local_path = os.path.join(io_dir, os.path.basename(value))
            subprocess.run(["curl", "-o", local_path, value], check=True)
            localized_inputs_dict[entry] = local_path
        # Non-files should get passed through unchanged.
        else:
            localized_inputs_dict[entry] = value

    return localized_inputs_dict


def main():
    """Run the WDL and indicate whether it succeeded or failed."""

    # Parse arguments
    parser = get_parser()
    args = parser.parse_args()

    io_dir = os.path.dirname(args.workflowSource)

    # Localize inputs
    localized_inputs_dict = localize_inputs(args.workflowInputs, io_dir)
    with open(os.path.join(io_dir, "inputs.json"), "w") as inputs_json:
        json.dump(localized_inputs_dict, inputs_json)

    # Extract dependencies
    if args.workflowDependencies:
        subprocess.run(["unzip", "-o", args.workflowDependencies], cwd=io_dir, check=True)

    # Run the WDL with cromwell
    proc = subprocess.run([
        "java", "-jar", CROWMELL_PATH, "run", args.workflowSource,
        "-i", os.path.join(io_dir, "inputs.json")],
                          cwd=io_dir)
    sys.exit(proc.returncode)

def get_parser():
    """Creates the ArgumentParser that will parse arguments to the docker container
    via an entrypoint.

    So, this is defining the interface that this execution environment presents to the
    portability service. It would be nice if we could use the GA4GH WES interface here,
    but that doesn't appear to support anything like "workflowDependencies", which are
    important for HCA workflows and the design of the tests.
    """

    parser = argparse.ArgumentParser()
    parser.add_argument("--workflowSource", required=True)
    parser.add_argument("--workflowDependencies", required=False)
    parser.add_argument("--workflowInputs", required=True)

    return parser

if __name__ == '__main__':
    main()
