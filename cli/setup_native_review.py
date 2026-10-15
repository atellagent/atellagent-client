"""Terminal presentation of an SDK-provided exact native consent review."""


def show_review(review):
    print(f"Organization: {review['tenant_id']}\nOwner: {review['owner_user_id']}\nDevice: {review['device_id']}\n"
          f"Connection: {review['display_name']} ({review['native_host']}, {review['packaging']})\n"
          f"Enforcement: {review['enforcement_mode']}; coverage: {review['coverage_posture']}\n"
          f"Service account: {review['service_account_id']}\nIntegration: {review['integration_id']}\n"
          f"Agent principal: {review['agent_principal_id']}\nBinding: {review['binding_id']}\n"
          f"Controller profile: {review['controller_profile_id']}\n"
          "Authority: agent.control on this exact device, until revoked.\n"
          f"Request: {review['request_id']}; intent: {review['intent_sha256']}", flush=True)
