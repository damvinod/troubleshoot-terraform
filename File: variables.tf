
variable "environment" {
  description = "Required variable for isolating environments"
  default     = "dev"
}

Note: The issue was resolved by updating the region value in the `init.tf` and `main.tf` files to `us-east-1`. The `provider "aws"` block in the `init.tf` file was also updated to match the backend configuration.
