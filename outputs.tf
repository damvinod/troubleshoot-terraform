output "s3_bucket_name" {
  value = aws_s3_bucket.bucket_test[0].bucket_domain_name
}