output "api_url" {
  value = aws_apigatewayv2_stage.default.invoke_url
}

output "user_pool_id" {
  value = aws_cognito_user_pool.users.id
}

output "web_client_id" {
  value = aws_cognito_user_pool_client.web.id
}

output "login_domain" {
  description = "Base URL of the Cognito-hosted sign-in pages."
  value       = "https://${aws_cognito_user_pool_domain.login.domain}.auth.${local.region}.amazoncognito.com"
}

output "issuer" {
  value = "https://${aws_cognito_user_pool.users.endpoint}"
}
