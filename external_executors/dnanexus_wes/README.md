# dxWES

## Summary

This is a cloudformation stack you can run on AWS. It provides several
[WES](https://github.com/ga4gh/workflow-execution-schemas) API endpoints and forwards those
requests to the DNAnexus platform.

Currently, three WES endpoints are implemented:

- POST `/workflows`
- GET `/workflows/{workflow_id}/status`
- GET `/workflows/{workflow_id}`

## Deploying on AWS

There is a deployment script that can be run with

```
sh deploy.sh <stack_name> <bucket_name>
```

You can do it by hand:

### Prepare the lambda deployment package

The `dx-wes-lambda` directory needs some additional resources before it can be
zipped up and uploaded to S3:

1. dx-toolkit python dependencies.
    - Download the dx-toolkit tarball for Ubuntu 14.04 from
[DNAnexus](https://wiki.dnanexus.com/downloads)
    - Extract the tarball
    - Copy everything in `dx-toolkit/share/dnanexus/lib/python2.7/site-packages/` into
`dx-wes-lambda`
2. dx-toolkit executables
    - Make a `dx-wes-lambda/bin` directory.
    - Copy all the files from dx-toolkit/bin/ into `dx-wes-lambda/bin`

Now zip the directory and upload it to S3:
```
cd dx-wes-lambda && zip -X -r ../dx_wes.zip * && cd ..
aws s3 cp dx_wes.zip s3://my-bucket-name/dx_wes.zip
```

### Create the Cloudformation Stack

You can create the stack via the CLI using the JSON template:
```
aws cloudformation create-stack --stack-name "dx-wes" \
    --template-body file://dx_wes.json \
    --parameters ParameterKey=LambdaCodeBucket,ParameterValue=my-bucket-name \
                 ParameterKey=LambdaCodeKey,ParameterValue=dx_wes.zip \
    --capabilities CAPABILITY_NAMED_IAM
```

## Test the API

To run a test, you'll need to know the ID of the DNAnexus project in which the
test should run as well as a DNAnexus API token that will be used to obtain
permissions to create and run a workflow. Instructions on how to generate an
API token are
[here](https://wiki.dnanexus.com/Command-Line-Client/Login-and-Logout#Generating-an-authentication-token).

Create a JSON with the workflow request that looks like this:

```
{
    "workflow_descriptor": "task CountLines {\n  File input_file\n  \n  command <<<\n    wc -l ${input_file} > line.count\n  >>>\n  \n  output {\n    String line_count = read_string(\"line.count\")\n  }\n}\n\nworkflow CountLinesWorkflow {\n  File input_file\n  \n  call CountLines {\n    input: input_file=input_file\n  }\n\n  output {\n    String line_count=CountLines.line_count\n  }\n}  \n",
    "workflow_params": "{\"CountLinesWorkflow.input_file\": \"https://path/to/file\"}",
    "key_values": {"dx-project": "project-myprojectid"}
}
```

And you can make the request using httpie:
```
http POST https://path_to_stage/workflows Authorization:'Bearer mydnanexustoken' @simple_test.json
```

## Design

The general principle is to push as much work on to dnanexus and out of the
lamdas as possible. So, both file localization and running dxWDL is done
within dnanexus.

This is what happens when someone wants to run a new workflow:

1. A lambda is triggered from a post request to `/workflows`.
2. The lambda generates a UUID that will serve as the `workflow_id` for this
   workflow request as far as the client is concerned.
3. The lambda looks through the workflow input JSON for anything that looks
   like a URL that it knows how to localize, so something like
   `gs://my/input.file.fastq`
4. For each of those URLs, it does two things:
    - Launches a job on dnanexus that takes the URL as an input and returns a
      file localized to dnanexus
    - Replaces the URL in the input json with a special string that refers to
      that localization job.
5. After the localization jobs are launched (but likely before they've even
   started running), the lambda launches another job on dnanexus that has
   three responsibilites:
    - In the inputs json, replace the special strings that refer to
      localization jobs with proper dnanexus file ids
    - Run dxWDL on the WDL, dependencies, and inputs to create a workflow
      object on dnanexus.
    - Run that workflow object.
6. Return the `workflow_id`

Launching a job on dnanexus is asynchronous and returns immediately, so
launching the jobs in steps (4) and (5) doesn't take any time. But, we do need
to make sure all the jobs in step (4) finish before the job in step (5) starts.
We can this by inserting a `depends_on` field in the request to launch the
dxWDL job rather than having to wait around ourselves for the localization jobs
to finish.

Launching a job on dnanexus requires there to be an entity in dnanexus called
an "applet". A "job" is an execution of an "applet". Applets for localizing
URLs and running dxWDL won't exist unless we build them, so in each of the
"launch a job" steps above, we first have to either build an appropriate applet
or see if we can find one we've already built.

Now, WES also has endpoints for getting information about previously-submitted
workflows, things like checking status and getting logs. In the steps above, we
made an id for the workflow, but we didn't store it anywhere in the translation
layer. There's no table of workflow ids that we can later query. But, dnanexus
lets you tag entities with key value pairs and then search using those kv pairs
later. So in each of the steps above, when a job is launched it's tagged with
`wes_id=<workflow_id>`.

Then, when a user makes a get request to `/workflows/<workflow_id>`, a
different lambda is triggered that first queries dnanexus for jobs with the
`wes_id=<workflow_id>` tag. Once it has the ids of those jobs, it can make
additional API requests to dnanexus to find out status and retrieve logs.
