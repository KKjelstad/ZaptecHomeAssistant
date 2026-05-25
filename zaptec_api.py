"""
Zaptec API Client
Handles authentication and API requests to Zaptec cloud services.
"""

import requests
import logging
import json
import os
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class ZaptecAPI:
    """Client for interacting with the Zaptec API."""
    
    def __init__(self, username: str, password: str, base_url: str = "https://api.zaptec.com",
                 token_cache_file: str = ".zaptec_token_cache.json"):
        """
        Initialize the Zaptec API client.
        
        Args:
            username: Zaptec account email
            password: Zaptec account password
            base_url: Base URL for Zaptec API
            token_cache_file: Path to file for caching OAuth tokens
        """
        self.username = username
        self.password = password
        self.base_url = base_url.rstrip('/')
        self.token_cache_file = Path(token_cache_file)
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None
        self.session = requests.Session()
        
        # Try to load cached token
        self._load_token_cache()
    
    def _load_token_cache(self):
        """Load cached OAuth token if available and valid."""
        if not self.token_cache_file.exists():
            logger.debug("No token cache file found")
            return
        
        try:
            with open(self.token_cache_file, 'r') as f:
                cache = json.load(f)
            
            # Check if token is still valid
            expiry = datetime.fromisoformat(cache.get('expiry', ''))
            if datetime.now() < expiry:
                self.access_token = cache.get('access_token')
                self.refresh_token = cache.get('refresh_token')
                self.token_expiry = expiry
                
                self.session.headers.update({
                    "Authorization": f"Bearer {self.access_token}"
                })
                
                logger.info("Loaded valid token from cache")
            else:
                logger.debug("Cached token expired")
                
        except Exception as e:
            logger.warning(f"Failed to load token cache: {e}")
    
    def _save_token_cache(self):
        """Save OAuth token to cache file."""
        if not self.access_token or not self.token_expiry:
            return
        
        try:
            cache = {
                'access_token': self.access_token,
                'refresh_token': self.refresh_token,
                'expiry': self.token_expiry.isoformat()
            }
            
            # Create the file 0600 from the start so the tokens are never
            # briefly world-readable (umask) between open() and chmod().
            fd = os.open(str(self.token_cache_file),
                         os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as f:
                json.dump(cache, f)
            os.chmod(self.token_cache_file, 0o600)  # tighten if it pre-existed

            logger.debug("Saved token to cache")
            
        except Exception as e:
            logger.warning(f"Failed to save token cache: {e}")
        
    def authenticate(self) -> bool:
        """
        Authenticate with the Zaptec API and obtain an access token.
        
        Returns:
            True if authentication successful, False otherwise
        """
        try:
            # Basic validation: ensure we have a password to perform the password grant.
            if not self.password:
                logger.error("No Zaptec password available for re-authentication. Provide ZAPTEC_PASSWORD environment variable or add 'password' to config.yaml.")
                return False
            url = f"{self.base_url}/oauth/token"
            data = {
                "grant_type": "password",
                "username": self.username,
                "password": self.password
            }
            
            logger.info("Authenticating with Zaptec API...")
            response = self.session.post(url, data=data, timeout=(5, 30))
            response.raise_for_status()
            
            token_data = response.json()
            self.access_token = token_data.get("access_token")
            self.refresh_token = token_data.get("refresh_token")
            expires_in = token_data.get("expires_in", 3600)
            self.token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)
            
            self.session.headers.update({
                "Authorization": f"Bearer {self.access_token}"
            })
            
            # Save token to cache
            self._save_token_cache()
            
            logger.info("Successfully authenticated with Zaptec API")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Authentication failed: {e}")
            return False
    
    def _refresh_access_token(self) -> bool:
        """Use the cached refresh token to get a new access token (no password)."""
        if not self.refresh_token:
            return False
        try:
            url = f"{self.base_url}/oauth/token"
            data = {"grant_type": "refresh_token", "refresh_token": self.refresh_token}
            logger.info("Refreshing Zaptec access token...")
            response = self.session.post(url, data=data, timeout=(5, 30))
            response.raise_for_status()

            token_data = response.json()
            self.access_token = token_data.get("access_token")
            if token_data.get("refresh_token"):
                self.refresh_token = token_data["refresh_token"]
            expires_in = token_data.get("expires_in", 3600)
            self.token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)
            self.session.headers.update({"Authorization": f"Bearer {self.access_token}"})
            self._save_token_cache()
            logger.info("Refreshed Zaptec access token")
            return True
        except requests.exceptions.RequestException as e:
            logger.warning(f"Token refresh failed, falling back to password grant: {e}")
            return False

    def _ensure_authenticated(self) -> bool:
        """Ensure we have a valid access token.

        On expiry, prefer the refresh-token grant (works without the password,
        so a token-cache-only deployment keeps running), then fall back to the
        password grant.
        """
        if self.access_token and self.token_expiry and datetime.now() < self.token_expiry:
            return True

        logger.info("Access token missing/expired; refreshing...")
        if self._refresh_access_token():
            return True
        return self.authenticate()
    
    def _make_request(self, method: str, endpoint: str, **kwargs) -> Optional[Dict]:
        """
        Make an authenticated request to the API with rate limit handling.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint
            **kwargs: Additional arguments to pass to requests
            
        Returns:
            Response JSON data or None on error
        """
        if not self._ensure_authenticated():
            return None
        
        url = f"{self.base_url}{endpoint}"
        max_retries = 3
        retry_delay = 1  # seconds
        # Never block forever on a stalled connection (connect, read) seconds.
        kwargs.setdefault("timeout", (5, 30))

        for attempt in range(max_retries):
            try:
                response = self.session.request(method, url, **kwargs)
                
                # Handle rate limiting (429 Too Many Requests)
                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', retry_delay * (2 ** attempt)))
                    logger.warning(f"Rate limited. Retrying after {retry_after} seconds...")
                    import time
                    time.sleep(retry_after)
                    continue
                
                response.raise_for_status()
                result = response.json() if response.content else {}
                logger.debug(f"API response type for {endpoint}: {type(result)}")
                return result
                
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429 and attempt < max_retries - 1:
                    # Exponential backoff for rate limiting
                    wait_time = retry_delay * (2 ** attempt)
                    logger.warning(f"Rate limit hit, waiting {wait_time}s before retry {attempt + 1}/{max_retries}")
                    import time
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error(f"API request failed: {method} {endpoint} - {e}")
                    if hasattr(e, 'response') and e.response is not None:
                        logger.error(f"Response status: {e.response.status_code}")
                        # Body may contain tokens/PII; keep it at DEBUG only.
                        logger.debug(f"Response body: {e.response.text[:500]}")
                    return None
                    
            except requests.exceptions.RequestException as e:
                logger.error(f"API request failed: {method} {endpoint} - {e}")
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)
                    logger.warning(f"Retrying in {wait_time}s...")
                    import time
                    time.sleep(wait_time)
                else:
                    return None
        
        return None
    
    def get_installations(self) -> Optional[List[Dict]]:
        """
        Get all installations accessible by the user.
        
        Returns:
            List of installation data or None on error
        """
        logger.debug("Fetching installations...")
        result = self._make_request("GET", "/api/installation")
        
        # Handle paginated response
        if isinstance(result, dict) and "Data" in result:
            return result["Data"]
        
        return result
    
    def get_chargers(self) -> Optional[List[Dict]]:
        """
        Get all chargers accessible by the user.
        
        Returns:
            List of charger data or None on error
        """
        logger.debug("Fetching chargers...")
        result = self._make_request("GET", "/api/chargers")
        
        # Log what we got for debugging
        if result is not None:
            logger.debug(f"get_chargers() returned type: {type(result)}")
            if isinstance(result, dict):
                logger.debug(f"get_chargers() dict keys: {result.keys()}")
                # Handle paginated response
                if "Data" in result:
                    data = result["Data"]
                    logger.debug(f"Extracted Data field: {len(data)} chargers")
                    return data
            elif isinstance(result, list) and len(result) > 0:
                logger.debug(f"get_chargers() list length: {len(result)}, first item type: {type(result[0])}")
        
        return result
    
    def get_charger_state(self, charger_id: str) -> Optional[Dict]:
        """
        Get the current state of a specific charger.
        
        Args:
            charger_id: The charger ID
            
        Returns:
            Charger state data or None on error
        """
        logger.debug(f"Fetching state for charger {charger_id}...")
        return self._make_request("GET", f"/api/chargers/{charger_id}/state")
    
    def start_charging(self, charger_id: str) -> bool:
        """
        Start charging on a specific charger.
        
        Args:
            charger_id: The charger ID
            
        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Starting charging on charger {charger_id}...")
        result = self._make_request("POST", f"/api/chargers/{charger_id}/sendCommand/501")
        return result is not None
    
    def stop_charging(self, charger_id: str) -> bool:
        """
        Stop charging on a specific charger.
        
        Args:
            charger_id: The charger ID
            
        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Stopping charging on charger {charger_id}...")
        result = self._make_request("POST", f"/api/chargers/{charger_id}/sendCommand/502")
        return result is not None
    
    def set_charge_current(self, charger_id: str, current: float) -> bool:
        """
        Set the maximum charge current.
        
        Args:
            charger_id: The charger ID
            current: Current in Amperes (between 6A and 32A typically)
            
        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Setting charge current to {current}A on charger {charger_id}...")
        # Use settings endpoint with setting ID 510 (CurrentInMaximum)
        result = self._make_request(
            "POST", 
            f"/api/chargers/{charger_id}/settings",
            json=[{"id": 510, "value": current}]
        )
        return result is not None
    
    def get_charge_history(self, charger_id: Optional[str] = None, 
                          from_date: Optional[datetime] = None,
                          to_date: Optional[datetime] = None) -> Optional[List[Dict]]:
        """
        Get charge history/completed sessions.
        
        Args:
            charger_id: Optional charger ID to filter by
            from_date: Optional start date
            to_date: Optional end date
            
        Returns:
            List of charge sessions or None on error
        """
        logger.debug("Fetching charge history...")
        params = {}
        
        if charger_id:
            params["ChargerId"] = charger_id
        if from_date:
            params["From"] = from_date.isoformat()
        if to_date:
            params["To"] = to_date.isoformat()
        
        result = self._make_request("GET", "/api/chargehistory", params=params)
        
        # Handle paginated response
        if isinstance(result, dict) and "Data" in result:
            return result["Data"]
        
        return result
