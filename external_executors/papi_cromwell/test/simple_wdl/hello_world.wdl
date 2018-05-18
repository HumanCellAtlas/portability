task count_lines {
  File input_file
  
  command {
    set -exuo pipefail
    line_count=$(wc -l < "${input_file}")
    echo "The file ${input_file} had $line_count lines." > count.txt
  }
  
  output {
    File line_count = "count.txt"
  }

  runtime {
    docker: "ubuntu:latest"
  }
}

workflow hello {
  File input_file

  call count_lines as cl {
    input:
      input_file = input_file
  }
  
  output {
    File line_count = cl.line_count
  }
} 
