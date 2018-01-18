import "smartseq2.wdl" as target
import "smartseq2_checker.wdl" as checker

workflow Ss2RsemSingleSample {
  # Inputs to the target workflow
  File fastq_read1
  File fastq_read2
  File gtf
  File ref_fasta
  File rrna_interval
  File ref_flat
  String star_genome
  String output_prefix
  String rsem_genome
  
  # Inputs to the checker tools
  String expected_aln_metrics_hash

  
  # Run the target workflow  
  call target.Ss2RsemSingleSample as ss2 {
    input:
      fastq_read1=fastq_read1,
      fastq_read2=fastq_read2,
      gtf=gtf,
      ref_fasta=ref_fasta,
      rrna_interval=rrna_interval,
      ref_flat=ref_flat,
      star_genome=star_genome,
      output_prefix=output_prefix,
      rsem_genome=rsem_genome
  }
  
  # Run the tool to check its outputs
  call checker.SmartSeq2Checker as check {
    input:
      aln_metrics=ss2.aln_metrics,
      expected_aln_metrics_hash=expected_aln_metrics_hash 
  }
}
