from typing import Any


def parse_initiate_auth_response(data: dict[str, Any]) -> dict[str, Any]: #Deconstruct the response parameters
    challenge_name = data.get("ChallengeName")
    session = data.get("Session")
    params = data.get("ChallengeParameters", {}) or {}

    return {
        "challenge_name": challenge_name,
        "session": session,
        "delivery_medium": params.get("CODE_DELIVERY_MEDIUM"),
        "delivery_destination": params.get("CODE_DELIVERY_DESTINATION"),
        "user_id_for_srp": params.get("USER_ID_FOR_SRP"),
        "allow_trusted": data.get("AllowTrusted"),
    }

from typing import Any


def parse_auth_result(data: dict[str, Any]) -> dict[str, Any]:
    auth = data.get("AuthenticationResult", {}) or {}

    result = {
        "access_token": auth.get("AccessToken"),
        "refresh_token": auth.get("RefreshToken"),
        "id_token": auth.get("IdToken"),
        "token_type": auth.get("TokenType"),
        "expires_in": auth.get("ExpiresIn"),
        "challenge_parameters": data.get("ChallengeParameters", {}),
        "mfa_phone_number_allow_list": data.get("MFAPhoneNumberAllowList", {}),
        "is_authenticated": bool(auth.get("AccessToken")),
    }

    return result