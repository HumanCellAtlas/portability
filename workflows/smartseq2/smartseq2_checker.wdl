task SmartSeq2Checker {
  File aln_metrics

  String expected_aln_metrics_hash

  command <<<
    aln_metrics_hash=$(tail -n +6 ${aln_metrics} | md5sum | awk '{print $1}')
    
    if [ "$aln_metrics_hash" != "$expected_aln_metrics_hash" ]; then
      exit 1
    fi
  >>>

  output {}
}
