"""
Manual integration test for the Salesforce ContentVersion download flow.

This script simulates what the worker does during the submit_bank_statements step:
  1. Authenticates with Salesforce using client_credentials
  2. Downloads a ContentVersion file by ID
  3. Verifies the response is a valid PDF

Usage:
  Set the CONTENT_VERSION_ID below, ensure SALESFORCE_* env vars are set, then run:
    python -m tests.test_sf_download

Required env vars (or .env file):
  SALESFORCE_INSTANCE_URL
  SALESFORCE_CLIENT_ID
  SALESFORCE_CLIENT_SECRET
  SALESFORCE_API_VERSION  (optional, defaults to v60.0)
"""

import os
import sys
import logging
from pathlib import Path

# Allow running from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rise.config.config import setup_logging, settings
from rise.salesforce.client import _get_access_token, download_content_version

setup_logging()
logger = logging.getLogger(__name__)

# ── Configure this before running ──────────────────────────────────────────────
CONTENT_VERSION_ID = "068Q100000BEFNBIA5"   # Replace with a real ContentVersionId
SAVE_TO = Path("C:\\Users\\Stepan\\Desktop\\test_pdf.pdf")    # Where to save the downloaded file locally
# ───────────────────────────────────────────────────────────────────────────────


def main():
    logger.info("=== Salesforce ContentVersion download test ===")
    logger.info("Instance URL : %s", settings.SALESFORCE_INSTANCE_URL)
    logger.info("API version  : %s", settings.SALESFORCE_API_VERSION)
    logger.info("ContentVersionId: %s", CONTENT_VERSION_ID)

    if not settings.SALESFORCE_INSTANCE_URL:
        raise RuntimeError("SALESFORCE_INSTANCE_URL is not set")
    if not settings.SALESFORCE_CLIENT_ID:
        raise RuntimeError("SALESFORCE_CLIENT_ID is not set")
    if not settings.SALESFORCE_CLIENT_SECRET:
        raise RuntimeError("SALESFORCE_CLIENT_SECRET is not set")

    # Step 1: Authenticate
    logger.info("\n--- Step 1: Obtaining access token ---")
    token = _get_access_token()
    logger.info("Token obtained: %s...%s", token[:8], token[-8:])

    # Step 2: Download the file
    logger.info("\n--- Step 2: Downloading ContentVersion ---")
    file_bytes = download_content_version(CONTENT_VERSION_ID)
    logger.info("Downloaded %s bytes", len(file_bytes))

    # Step 3: Validate it's a PDF
    if not file_bytes.startswith(b"%PDF"):
        raise ValueError(f"Downloaded content does not start with %PDF header — got: {file_bytes[:20]}")
    logger.info("PDF header validated OK")

    # Step 4: Save locally
    SAVE_TO.write_bytes(file_bytes)
    logger.info("Saved to: %s", SAVE_TO)

    logger.info("\n=== Test passed ===")


if __name__ == "__main__":
    main()