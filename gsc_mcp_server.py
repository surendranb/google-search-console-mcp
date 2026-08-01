# MCP 2.0 (2026-07-28) — official mcp SDK v2. Ported off the standalone
# `fastmcp` package: its latest release pins mcp<2.0 and cannot speak the
# 2026-07-28 spec, and the released decorator monkey-patch registered zero
# tools on current fastmcp. See gsc_telemetry.py for the telemetry rationale.
from mcp.server.mcpserver import MCPServer, Context
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials
import os
import sys
import json
import time
import inspect
import functools
from pathlib import Path
from datetime import datetime, timedelta

from gsc_telemetry import (
    MCP_SERVER_VERSION,
    send_telemetry,
    capture_client_info,
    announce_first_run,
)

# Configuration from environment variables
CREDENTIALS_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
GSC_SITE_URL = os.getenv("GSC_SITE_URL")  # e.g., "https://example.com/"
SERVER_INIT_ERROR = None

def _guided_error(what, steps):
    step_text = " ".join(f"({i}) {s}" for i, s in enumerate(steps, 1))
    return (f"[SETUP BLOCKED] {what}  "
            f"RETRYING WON'T HELP — do not re-call data tools.  "
            f"WHAT MUST HAPPEN: {step_text}")

if not CREDENTIALS_PATH:
    SERVER_INIT_ERROR = _guided_error(
        "No Google credentials are configured — GOOGLE_APPLICATION_CREDENTIALS is unset.",
        ["Point GOOGLE_APPLICATION_CREDENTIALS at a Google service-account JSON key."]
    )
elif not GSC_SITE_URL:
    SERVER_INIT_ERROR = _guided_error(
        "GSC_SITE_URL environment variable not set.",
        ["Please set it to your verified site URL (e.g., https://example.com/)."]
    )
elif CREDENTIALS_PATH and not os.path.exists(CREDENTIALS_PATH):
    SERVER_INIT_ERROR = _guided_error(
        f"Credentials file not found at '{CREDENTIALS_PATH}'.",
        ["Verify the file exists at that exact absolute path."]
    )

GSC_MCP_INSTRUCTIONS = """\
Google Search Console Data API access for AI agents — query with schema-accurate names, interpret with skills.

How to work with this server:
1. DISCOVER names before querying: call list_available_dimensions and list_available_metrics to get the exact valid dimensions and metrics in THIS property. Never guess.
2. INTERPRET with skills: for anything beyond a raw pull, call search_skills first — the skills library has proven field combinations and how to read the result.
"""

# Initialize the MCP 2.0 server. version= is required (v2 stopped defaulting it);
# instructions passed by keyword (positional would land in `title`).
mcp = MCPServer(
    "Google Search Console",
    version=MCP_SERVER_VERSION if MCP_SERVER_VERSION != "unknown" else "0.0.0",
    instructions=GSC_MCP_INSTRUCTIONS,
)

# --- TELEMETRY INSTRUMENTATION ---
# Every tool is wrapped by @instrument (applied UNDER @mcp.tool()). The wrapper
# reads the connected client's identity from the per-request `ctx` (dual-era:
# 2026 stateless _meta or the legacy handshake session — see gsc_telemetry) and
# fires a `tool_executed` event. functools.wraps preserves the tool signature so
# MCPServer builds the correct input schema; `ctx` is auto-excluded from it.
#
# This replaces the released `mcp.tool = _telemetry_tool` monkey-patch, which
# registered ZERO tools on current fastmcp / mcp 2.0 (verified empirically).


def fire_skill_tip(ctx=None, message="", skill=None, trigger="", tool_name=""):
    """Nudge the model toward skills; logging goes to the client if it opted in
    (ctx.info still exists in mcp 2.0 but is deprecated/opt-in per the 2026 spec)."""
    if ctx is not None:
        try:
            ctx.info(message)
        except Exception:
            pass
    send_telemetry("skill_tip_shown", {
        "tool_name": tool_name,
        "skill_suggested": skill or "generic",
        "trigger": trigger,
        "ctx_available": ctx is not None,
    })


def instrument(func):
    """Wrap a tool with fire-and-forget telemetry. Signature-preserving."""
    @functools.wraps(func)
    def wrapper(*w_args, **w_kwargs):
        start_time = time.time()
        status = "success"
        error_category = None
        rows_returned = 0
        result = None

        # Capture client identity from ctx (dual-era) as early as possible.
        ctx = w_kwargs.get("ctx")
        if ctx is None:
            for a in w_args:
                if isinstance(a, Context):
                    ctx = a
                    break
        if ctx is not None:
            capture_client_info(ctx)

        try:
            if SERVER_INIT_ERROR:
                status = "error"
                error_category = "InitError"
                return f"Configuration Error: {SERVER_INIT_ERROR}. Please instruct the user to fix their setup."

            result = func(*w_args, **w_kwargs)

            if isinstance(result, dict):
                if "error" in result:
                    status = "error"
                    err_str = str(result["error"])
                    if "PermissionDenied" in err_str or "403" in err_str:
                        error_category = "IAMError"
                    else:
                        error_category = "APIError"
                elif "metadata" in result:
                    rows_returned = result.get("metadata", {}).get("total_rows", 0)

            return result
        except Exception as e:
            status = "exception"
            error_category = e.__class__.__name__
            raise
        finally:
            latency_ms = int((time.time() - start_time) * 1000)
            is_ci = os.getenv("CI", "false").lower() == "true" or os.getenv("GITHUB_ACTIONS", "false").lower() == "true"
            tz_name = time.tzname[0] if hasattr(time, "tzname") and time.tzname else "unknown"

            props = {
                "tool_name": func.__name__,
                "status": status,
                "latency_ms": latency_ms,
                "is_ci": is_ci,
                "timezone": tz_name,
                "rows_returned": rows_returned,
            }

            if func.__name__ == "get_search_analytics":
                try:
                    sig = inspect.signature(func)
                    bound = sig.bind(*w_args, **w_kwargs)
                    bound.apply_defaults()
                    args_dict = bound.arguments
                    props["dimensions_count"] = len(args_dict.get("dimensions") or [])
                    props["has_filters"] = bool(args_dict.get("filters"))
                    props["search_type"] = args_dict.get("search_type")
                except Exception:
                    pass

            if error_category:
                props["error_category"] = error_category

            if SERVER_INIT_ERROR:
                props["error_message"] = str(SERVER_INIT_ERROR)
            elif status == "exception":
                _, exc_value, _ = sys.exc_info()
                props["error_message"] = str(exc_value) if exc_value else "Unknown Exception"
            elif isinstance(result, dict) and "error" in result:
                props["error_message"] = str(result["error"])

            send_telemetry("tool_executed", props)

    return wrapper
# --- END TELEMETRY INSTRUMENTATION ---

# Initialize Google Search Console API client
def get_gsc_service():
    """Initialize and return Google Search Console API service"""
    try:
        credentials = Credentials.from_service_account_file(CREDENTIALS_PATH)
        service = build('searchconsole', 'v1', credentials=credentials)
        return service
    except Exception as e:
        print(f"Error initializing GSC service: {str(e)}", file=sys.stderr)
        raise

# Load dimensions and metrics from JSON files
def load_gsc_dimensions():
    """Load available GSC dimensions from JSON file"""
    try:
        script_dir = Path(__file__).parent
        with open(script_dir / "gsc_dimensions.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print("Warning: gsc_dimensions.json not found", file=sys.stderr)
        return {}

def load_gsc_metrics():
    """Load available GSC metrics from JSON file"""
    try:
        script_dir = Path(__file__).parent
        with open(script_dir / "gsc_metrics.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print("Warning: gsc_metrics.json not found", file=sys.stderr)
        return {}

@mcp.tool()
@instrument
def list_gsc_sites():
    """
    List all sites verified in Google Search Console.
    
    Returns:
        List of verified sites with their permission levels.
    """
    try:
        service = get_gsc_service()
        sites = service.sites().list().execute()
        
        result = []
        for site in sites.get('siteEntry', []):
            result.append({
                'siteUrl': site['siteUrl'],
                'permissionLevel': site['permissionLevel']
            })
        
        return result
    except Exception as e:
        return {"error": f"Error fetching sites: {str(e)}"}

@mcp.tool()
@instrument
def list_available_dimensions(ctx: Context = None):
    """
    List all available GSC dimensions with their descriptions.
    
    Returns:
        List of dimension objects with api_name and description.
    """
    dimensions = load_gsc_dimensions()
    
    # NUDGE the model to use search_skills when it discovers schema
    if ctx:
        fire_skill_tip(
            ctx=ctx,
            message="PRO TIP: Don't guess which dimensions to combine for strategic analysis. Use `search_skills` to see proven GSC query patterns.",
            trigger="schema_discovery",
            tool_name="list_available_dimensions"
        )
        
    return dimensions.get('dimensions', [])

@mcp.tool()
@instrument
def list_available_metrics():
    """
    List all available GSC metrics with their descriptions.
    
    Returns:
        List of metric objects with api_name and description.
    """
    metrics = load_gsc_metrics()
    return metrics.get('metrics', [])

@mcp.tool()
@instrument
def search_skills():
    """
    List available analytical skills (playbooks) for Google Search Console.
    Use this to learn proven field combinations and how to interpret GSC data for specific SEO tasks.
    """
    skills_dir = Path(__file__).parent / "skills"
    if not skills_dir.exists():
        return {"skills": [], "message": "No skills directory found."}
        
    available_skills = []
    for md_file in skills_dir.glob("*.md"):
        try:
            content = md_file.read_text()
            # Simple frontmatter parsing
            title = md_file.stem
            desc = ""
            for line in content.splitlines():
                if line.startswith("title:"): title = line.split(":", 1)[1].strip()
                if line.startswith("description:"): desc = line.split(":", 1)[1].strip()
            
            available_skills.append({
                "id": md_file.name,
                "title": title,
                "description": desc,
                "content": content
            })
        except Exception:
            pass
            
    return {"skills": available_skills}

@mcp.tool()
@instrument
def get_search_analytics(
    dimensions=["query"],
    start_date=None,
    end_date=None,
    filters=None,
    search_type="web",
    row_limit=1000,
    start_row=0,
    summary_only=False,
    ctx: Context = None
):
    """
    Retrieve Google Search Console search analytics data.
    
    Args:
        dimensions: List of dimensions from: country, device, page, query, searchAppearance, date
        start_date: Start date in YYYY-MM-DD format (defaults to 30 days ago)
        end_date: End date in YYYY-MM-DD format (defaults to 3 days ago)
        filters: List of filter objects (e.g., [{"dimension": "country", "operator": "equals", "expression": "usa"}])
        search_type: Type of search ('web', 'image', 'video', 'news', 'discover', 'googleNews')
        row_limit: Maximum number of rows to return (max 25000)
        start_row: Starting row for pagination (0-based)
        summary_only: If True, returns only aggregated totals (Token Efficient)
        
    Returns:
        Dictionary containing search analytics data with clicks, impressions, ctr, and position metrics.
    """
    try:
        try: row_limit = int(row_limit)
        except ValueError: row_limit = 1000
        
        try: start_row = int(start_row)
        except ValueError: start_row = 0
        
        if ctx and row_limit > 5000:
            fire_skill_tip(
                ctx=ctx,
                message="Pulling >5000 rows. Did you know you can use skills like 'brand_visibility.md' to pre-aggregate data server-side?",
                trigger="large_query",
                tool_name="get_search_analytics"
            )
            
        # Handle string input for dimensions
        if isinstance(dimensions, str):
            try:
                dimensions = json.loads(dimensions)
                if not isinstance(dimensions, list):
                    dimensions = [str(dimensions)]
            except json.JSONDecodeError:
                dimensions = [d.strip() for d in dimensions.split(',')]
        
        # Validate dimensions
        valid_dimensions = ["country", "device", "page", "query", "searchAppearance", "date"]
        for dim in dimensions:
            if dim not in valid_dimensions:
                return {"error": f"Invalid dimension '{dim}'. Valid dimensions: {valid_dimensions}"}
        
        # Set default dates if not provided
        if not start_date:
            start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        if not end_date:
            end_date = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')
        
        # Handle filters
        request_filters = []
        if filters:
            if isinstance(filters, str):
                try:
                    filters = json.loads(filters)
                except json.JSONDecodeError:
                    return {"error": "Invalid filters format. Expected JSON array."}
            
            for filter_item in filters:
                # Validate filter dimension
                filter_dim = filter_item.get('dimension')
                if filter_dim not in valid_dimensions:
                    return {"error": f"Invalid filter dimension '{filter_dim}'. Valid dimensions: {valid_dimensions}"}
                
                request_filters.append({
                    'dimension': filter_dim,
                    'operator': filter_item.get('operator', 'equals'),
                    'expression': filter_item.get('expression')
                })
        
        # Validate search type
        valid_search_types = ["web", "image", "video", "news", "discover", "googleNews"]
        if search_type not in valid_search_types:
            return {"error": f"Invalid search_type '{search_type}'. Valid types: {valid_search_types}"}
        
        # Build the request
        request = {
            'startDate': start_date,
            'endDate': end_date,
            'dimensions': dimensions,
            'searchType': search_type,
            'rowLimit': min(row_limit, 25000),  # GSC API limit
            'startRow': start_row
        }
        
        if request_filters:
            request['dimensionFilterGroups'] = [{
                'filters': request_filters
            }]
        
        # Execute the request
        service = get_gsc_service()
        response = service.searchanalytics().query(
            siteUrl=GSC_SITE_URL,
            body=request
        ).execute()
        
        rows = response.get('rows', [])
        
        if summary_only:
            return {
                "summary": {
                    "total_clicks": sum(r.get('clicks', 0) for r in rows),
                    "total_impressions": sum(r.get('impressions', 0) for r in rows),
                    "avg_ctr": round((sum(r.get('clicks', 0) for r in rows) / sum(r.get('impressions', 0) for r in rows)) * 100, 2) if sum(r.get('impressions', 0) for r in rows) > 0 else 0,
                    "row_count": len(rows)
                }
            }
        
        # Format the response
        result = {
            'metadata': {
                'site_url': GSC_SITE_URL,
                'start_date': start_date,
                'end_date': end_date,
                'dimensions': dimensions,
                'search_type': search_type,
                'total_rows': len(rows),
                'row_limit': row_limit,
                'start_row': start_row
            },
            'data': []
        }
        
        for row in rows:
            data_row = {}
            
            # Add dimension values
            if 'keys' in row:
                for i, dimension in enumerate(dimensions):
                    if i < len(row['keys']):
                        data_row[dimension] = str(row['keys'][i])
            
            # Add metric values (all GSC metrics are always returned)
            data_row['clicks'] = row.get('clicks', 0)
            data_row['impressions'] = row.get('impressions', 0)
            data_row['ctr'] = round(row.get('ctr', 0.0) * 100, 2)  # Convert to percentage
            data_row['position'] = round(row.get('position', 0.0), 1)
            
            result['data'].append(data_row)
        
        return result
        
    except Exception as e:
        error_message = f"Error fetching GSC data: {str(e)}"
        print(error_message, file=sys.stderr)
        return {"error": error_message}

@mcp.tool()
@instrument
def get_sitemaps():
    """
    Get all sitemaps for the configured site.
    
    Returns:
        List of sitemaps with their status and details.
    """
    try:
        service = get_gsc_service()
        sitemaps = service.sitemaps().list(siteUrl=GSC_SITE_URL).execute()
        
        result = []
        for sitemap in sitemaps.get('sitemap', []):
            result.append({
                'path': sitemap.get('path'),
                'lastSubmitted': sitemap.get('lastSubmitted'),
                'isPending': sitemap.get('isPending', False),
                'isSitemapsIndex': sitemap.get('isSitemapsIndex', False),
                'type': sitemap.get('type'),
                'lastDownloaded': sitemap.get('lastDownloaded'),
                'warnings': sitemap.get('warnings', 0),
                'errors': sitemap.get('errors', 0)
            })
        
        return result
        
    except Exception as e:
        return {"error": f"Error fetching sitemaps: {str(e)}"}

@mcp.tool()
@instrument
def submit_sitemap(sitemap_url):
    """
    Submit a sitemap to Google Search Console.
    
    Args:
        sitemap_url: Full URL of the sitemap to submit
        
    Returns:
        Success message or error details.
    """
    try:
        service = get_gsc_service()
        service.sitemaps().submit(
            siteUrl=GSC_SITE_URL,
            feedpath=sitemap_url
        ).execute()
        
        return {"success": f"Sitemap submitted successfully: {sitemap_url}"}
        
    except Exception as e:
        return {"error": f"Error submitting sitemap: {str(e)}"}

@mcp.tool()
@instrument
def delete_sitemap(sitemap_url):
    """
    Delete a sitemap from Google Search Console.
    
    Args:
        sitemap_url: Full URL of the sitemap to delete
        
    Returns:
        Success message or error details.
    """
    try:
        service = get_gsc_service()
        service.sitemaps().delete(
            siteUrl=GSC_SITE_URL,
            feedpath=sitemap_url
        ).execute()
        
        return {"success": f"Sitemap deleted successfully: {sitemap_url}"}
        
    except Exception as e:
        return {"error": f"Error deleting sitemap: {str(e)}"}

def main():
    """Main entry point for the MCP server"""
    # Use stdio transport ONLY - this is critical for MCP with Claude
    print("Starting GSC MCP server...", file=sys.stderr)
    announce_first_run()
    send_telemetry("mcp_started", {"config_status": "error" if SERVER_INIT_ERROR else "success"})
    # mcp.run() defaults to stdio; pass explicitly for clarity/parity with v1.
    mcp.run(transport="stdio")

# Start the server when run directly
if __name__ == "__main__":
    main()