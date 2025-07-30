resource "aws_s3_bucket" "bucket_test" {
  bucket = "test-terraform-bucket-terraform-1234567890"
  acl = "private"
}