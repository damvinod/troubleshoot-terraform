variable "environment" {
  description = "Required variable for isolating environments"
  default     = "dev"
  lifecyle {
    ignore_changes = true
  }
}