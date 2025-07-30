terraform

output "s3_bucket_name" {
  value = aws_s3_bucket.bucket_test.bucket_domain_name
}