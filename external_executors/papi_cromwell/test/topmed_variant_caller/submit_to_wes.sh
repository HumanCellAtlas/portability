http POST "$WES_URL"/workflows workflow_descriptor=@topmed_freeze3_calling.wdl workflow_params:=@inputs.json X-API-KEY:"$API_KEY"
