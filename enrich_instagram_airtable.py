"""
enrich_instagram_airtable.py

Fetches records from the specific Instagram view in Airtable, queries the Simple Instagram API
via RapidAPI (rotating 11 keys to avoid limits), and performs bulk PATCH updates 
(10 records per request) to populate Instagram metadata including followers, bio, and contact info.

Usage:
    python3 enrich_instagram_airtable.py --limit 10
    python3 enrich_instagram_airtable.py --all
"""

import sys
import argparse
import requests
import json
import time
import re
from datetime import date
from threading import Lock
import os

# ── Config ─────────────────────────────────────────────────────────────────────
# Configuration prioritizes Environment Variables (for GitHub Actions)
# FALLBACKS REMOVED FOR SECURITY (GitHub Push Protection)

AIRTABLE_API_KEY = os.environ.get("AIRTABLE_API_KEY")
BASE_ID          = os.environ.get("AIRTABLE_BASE_ID", "appuvMdeNd1hgrGNU")
TABLE_ID         = os.environ.get("AIRTABLE_TABLE_ID", "tblClKjrh2wJwxXuI")
VIEW_ID          = os.environ.get("AIRTABLE_VIEW_ID", "viwVsDgCKCtD9Rzvg")

if not AIRTABLE_API_KEY:
    print("[ERROR] AIRTABLE_API_KEY environment variable not set.")
    sys.exit(1)

AIRTABLE_HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_API_KEY}",
    "Content-Type": "application/json"
}

# RapidAPI Keys - can be a comma-separated string in env
env_keys = os.environ.get("RAPIDAPI_KEYS")
if env_keys:
    RAPIDAPI_KEYS = [k.strip() for k in env_keys.split(",") if k.strip()]
else:
    # No fallbacks allowed on GitHub
    print("[ERROR] RAPIDAPI_KEYS environment variable not set.")
    sys.exit(1)
# Remove duplicates and empty strings
RAPIDAPI_KEYS = list(dict.fromkeys([k.strip() for k in RAPIDAPI_KEYS if k.strip()]))

key_lock = Lock()
key_index = 0

def get_next_rapidapi_key():
    global key_index
    with key_lock:
        key = RAPIDAPI_KEYS[key_index]
        key_index = (key_index + 1) % len(RAPIDAPI_KEYS)
        return key

def update_running_stat(existing_json_str, new_value_str, date_str):
    """Maintain a JSON array of running statistics indicating daily changes."""
    try: new_val = int(new_value_str)
    except: return existing_json_str
        
    try: arr = json.loads(existing_json_str) if existing_json_str else []
    except: arr = []
        
    if arr and arr[-1].get("date") == date_str:
        last_entry = arr[-2] if len(arr) > 1 else None
        last_val = last_entry.get("count", new_val) if last_entry else new_val
        diff = new_val - last_val
        percent = (diff / last_val * 100) if last_val > 0 else 0.0
        arr[-1] = {"date": date_str, "count": new_val, "diff": diff, "percent": round(percent, 2)}
        return json.dumps(arr)
        
    last_val = arr[-1].get("count", new_val) if arr else new_val
    diff = new_val - last_val
    percent = (diff / last_val * 100) if last_val > 0 else 0.0
    arr.append({"date": date_str, "count": new_val, "diff": diff, "percent": round(percent, 2)})
    return json.dumps(arr)

def extract_username(url_or_handle):
    """Extract Instagram username from a URL or raw handle."""
    if not url_or_handle: return None
    url_or_handle = str(url_or_handle).strip()
    if not url_or_handle: return None
    
    # regex for instagram urls
    match = re.search(r"(?:instagram\.com|instagr\.am)/(?:p/|reels/|reel/|stories/)?([a-zA-Z0-9._]+)", url_or_handle)
    if match:
        return match.group(1)
    
    # clean handle
    clean = url_or_handle.replace("@", "").split("?")[0].strip("/")
    if "/" in clean: clean = clean.split("/")[-1]
    return clean

def extract_email_from_text(text):
    """Fallback: extract email from bio if present."""
    if not text: return None
    match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
    return match.group(0) if match else None

# ── API Helpers ────────────────────────────────────────────────────────────────

def fetch_instagram_profile(username: str) -> dict:
    """Query Simple Instagram RapidAPI for profile details."""
    if not username:
        return None

    key = get_next_rapidapi_key()
    url = "https://simple-instagram-api.p.rapidapi.com/account-info"
    headers = {
        "X-RapidAPI-Key": key,
        "X-RapidAPI-Host": "simple-instagram-api.p.rapidapi.com"
    }
    params = {"username": username}

    try:
        r = requests.get(url, headers=headers, params=params, timeout=20)
        if r.status_code == 200:
            return r.json()
        elif r.status_code == 429:
            print(f"  [WARN] Rate limit hit on Key ending in ...{key[-4:]}. Throttling...")
            time.sleep(2)
        elif r.status_code == 404:
            print(f"  [WARN] User Not Found: {username}")
            return {"status": "not_found"}
        else:
            print(f"  [WARN] Instagram API returned {r.status_code} for {username}")
    except Exception as e:
        print(f"  [WARN] Instagram Request Failed: {e}")
        
    return None

def update_records_bulk(records_batch: list):
    """Update up to 10 records at once in Airtable."""
    if not records_batch:
        return True, {}
        
    url = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"
    r = requests.patch(url, headers=AIRTABLE_HEADERS, json={"records": records_batch}, timeout=15)
    return r.status_code == 200, r.json()

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Enrich Instagram profiles in Airtable.")
    parser.add_argument("--limit", type=int, default=None, help="Stop after processing N records")
    parser.add_argument("--all", action="store_true", help="Process all records in the view")
    args = parser.parse_args()

    if not args.all and args.limit is None:
        args.limit = 10 

    print(f"Starting Instagram enrichment from view {VIEW_ID}...")
    
    ok_count = 0
    err_count = 0
    processed_this_run = 0
    batch_queue = []
    
    url = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"
    params = {
        "pageSize": 40,
        "view": VIEW_ID
    }

    today_str = date.today().isoformat()

    while True:
        try:
            r = requests.get(url, headers=AIRTABLE_HEADERS, params=params, timeout=15)
            data = r.json()
        except Exception as e:
            print(f"[RETRY] Connection error: {e}. Sleeping 5s...")
            time.sleep(5)
            continue

        if "error" in data:
            if data["error"].get("type") == "LIST_RECORDS_ITERATOR_NOT_AVAILABLE":
                params.pop("offset", None)
                time.sleep(1)
                continue
            else:
                print(f"[ERROR] Airtable fetch: {data}")
                break
        
        page_records = data.get("records", [])
        if not page_records:
            break
            
        print(f"--- Processing page of {len(page_records)} records ---")
        
        for record in page_records:
            rec_id = record["id"]
            fields = record.get("fields", {})
            
            # Identify the target handle
            raw_ig = (
                fields.get("Soc IG Username") or 
                fields.get("Soc Instagram Url") or 
                fields.get("Soc IG URL") or 
                fields.get("Instagram") or 
                fields.get("IG Handle") or 
                fields.get("Instagram URL")
            )
            username = extract_username(raw_ig)
            
            processed_this_run += 1
            name = fields.get("Name") or username or "Unknown"
            print(f"[{processed_this_run}] {name} (@{username})")

            if not username:
                print("  Skipping — No Instagram handle found")
                continue

            api_data = fetch_instagram_profile(username)

            if api_data and api_data.get("status") == "not_found":
                batch_queue.append({"id": rec_id, "fields": {"Soc IG Status": "Not Found", "Soc IG Check": today_str}})
            elif api_data and "id" in api_data:
                # Core Stats
                followers = api_data.get("edge_followed_by", {}).get("count")
                following = api_data.get("edge_follow", {}).get("count")
                posts     = api_data.get("edge_owner_to_timeline_media", {}).get("count")
                bio       = api_data.get("biography", "")
                
                # Contact Info
                email     = api_data.get("business_email") or extract_email_from_text(bio)
                phone     = api_data.get("business_phone_number")
                
                update_data = {
                    "Soc IG Account Id": str(api_data.get("id")),
                    "Soc IG Username": api_data.get("username"),
                    "Soc IG Full Name": api_data.get("full_name"),
                    "Soc IG Followers": str(followers) if followers is not None else None,
                    "Soc IG Following": str(following) if following is not None else None,
                    "Soc IG Posts": str(posts) if posts is not None else None,
                    "Soc IG Bio": bio,
                    "Soc IG Website": api_data.get("external_url"),
                    "Soc IG Profile Pic": api_data.get("profile_pic_url_hd"),
                    "Soc IG Is Private": "TRUE" if api_data.get("is_private") else "FALSE",
                    "Soc IG Is Verified": "TRUE" if api_data.get("is_verified") else "FALSE",
                    "Soc IG Category": api_data.get("business_category_name"),
                    "Soc IG Email": email,
                    "Soc IG Phone": phone,
                    "Soc IG Status": "Success",
                    "Soc IG Check": today_str,
                    "Soc IG Raw": json.dumps(api_data)[:100000] # Cap raw data
                }
                
                # Maintenance of History Array
                if followers is not None:
                    update_data["Soc IG Fan Array"] = update_running_stat(
                        fields.get("Soc IG Fan Array"), str(followers), today_str
                    )
                
                # Clean empty values
                update_data = {k: v for k, v in update_data.items() if v is not None and v != ""}
                batch_queue.append({"id": rec_id, "fields": update_data})
            else:
                print(f"  API returned error or no data for {username}")
                err_count += 1

            if len(batch_queue) >= 10:
                print(f"  --> Sending bulk update for {len(batch_queue)} records...")
                success, resp = update_records_bulk(batch_queue)
                if success:
                    print("  ✅ Batch updated successfully")
                    ok_count += len(batch_queue)
                else:
                    print(f"  ❌ Batch update failed: {resp}")
                    err_count += len(batch_queue)
                batch_queue.clear()
                time.sleep(0.5)

            if args.limit and processed_this_run >= args.limit:
                break
            time.sleep(0.1)

        # Flush remaining
        if batch_queue:
            print(f"  --> Final flush for page ({len(batch_queue)} records)...")
            success, resp = update_records_bulk(batch_queue)
            if success:
                print("  ✅ Batch updated successfully")
                ok_count += len(batch_queue)
            else:
                print(f"  ❌ Batch update failed: {resp}")
                err_count += len(batch_queue)
            batch_queue.clear()

        if args.limit and processed_this_run >= args.limit:
            break
            
        offset = data.get("offset")
        if not offset:
            break
        params["offset"] = offset

    print(f"\nDone. ✅ Enriched: {ok_count} | ❌ Failed/Skipped: {err_count}")

if __name__ == "__main__":
    main()
