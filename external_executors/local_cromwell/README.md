# Local Cromwell Service

This deploys an API on AWS that looks a little bit like WES and runs WDLs using
Cromwell with a local backend. Its purpose is to provide one portability test
environment for the HCA's secondary analysis workflows.

There are two files:

### local_cromwell.py
This defines two Lambda functions that are responsible for handling two
request types: POSTs to `/workflows` and GETs to
`/workflows/{workflow_id}/status`. These two calls are enough to launch a test
workflow and see if it worked.

A POST to `/workflows` is handled by the `workflows_post` function. That
launches an EC2 instance that will run Cromwell and record outcomes in a
database. A GET to `/workflows/{workflow_id}/status` is handled by the
`workflow_status` function. That queries the database for events associated
with the `workflow_id` and reports its status using WES terms. (NB: The 404
response doesn't quite work for this yet.)

### local_cromwell_template.json
This is an AWS Cloudformation template for the local Cromwell service. It
deploys an API to a "test" stage that has the two endpoints mentioned above.
The template handles all the AWS concerns like setting up roles, policies,
tables, functions, etc. The template contains a definition of the API using
Swagger.

## Deploying
There are two steps to deploying the service:
1. Zip and upload the Lambda functions. Just `zip local_cromwell.zip
   local_cromwell.py` and upload the zip file to an S3 bucket.
2. Use AWS Cloudformation to create a stack using
   `local_cromwell_template.json`. This can be done through the AWS console.
   You will have to supply the bucket and file name for the
   `local_cromwell.zip` file that you uploaded.
