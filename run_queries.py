import urllib.request
import json
import os

POSTHOG_URL = "https://us.posthog.com/api/projects/489528/query/"
POSTHOG_HEADERS = {
    "Authorization": "Bearer " + os.environ["POSTHOG_PERSONAL_KEY"],
    "Content-Type": "application/json",
    "User-Agent": "gsc-report/1.0"
}

SERVER = "google-search-console-mcp"
INTERNAL = "properties.traffic_class!='internal' OR properties.traffic_class IS NULL"
NONFUZZ = "properties.mcp_client_name IS NULL OR (properties.mcp_client_name NOT ILIKE '%fuzz%' AND properties.mcp_client_name NOT ILIKE '%test%')"
DAY = "toStartOfDay(now()+toIntervalMinute(330))-toIntervalMinute(330)"

queries = {
    "Q1_usage": f"""SELECT uniqIf(distinct_id, event='tool_executed' AND properties.run_context IN ('terminal','desktop')) AS human_active, uniqIf(distinct_id, event='tool_executed' AND (properties.run_context NOT IN ('terminal','desktop') OR properties.run_context IS NULL)) AS agent_active, uniqIf(distinct_id, event='tool_executed' AND properties.status='success') AS ok_installs, countIf(event='server_first_install') AS new_installs, countIf(event='tool_executed') AS calls, countIf(event='tool_executed' AND properties.status='success') AS ok_calls, countIf(event='tool_executed' AND properties.error_category='ValidationError') AS validation_fail, countIf(event='tool_executed' AND properties.error_category IN ('IAMError','APIError')) AS google_fail, countIf(event='mcp_started' AND properties.config_status='error') AS error_boot FROM events WHERE timestamp >= {DAY}-toIntervalDay(1) AND timestamp < {DAY} AND properties.mcp_server_name='{SERVER}' AND ({INTERNAL}) AND ({NONFUZZ})""",

    "Q2_boots_vs_usage": f"""SELECT countIf(event='mcp_started') AS boots, countIf(event='tool_executed') AS calls, round(100*countIf(event='tool_executed')/countIf(event='mcp_started'),1) AS calls_per_boot_pct FROM events WHERE timestamp >= {DAY}-toIntervalDay(1) AND timestamp < {DAY} AND properties.mcp_server_name='{SERVER}' AND ({INTERNAL}) AND ({NONFUZZ})""",

    "Q3_harness": f"""SELECT CASE WHEN properties.mcp_client_name LIKE 'local-agent-mode%' THEN 'claude_cowork' ELSE coalesce(properties.mcp_client_name,'(none)') END AS harness, uniq(distinct_id) AS installs, count() AS calls FROM events WHERE timestamp >= {DAY}-toIntervalDay(1) AND timestamp < {DAY} AND event='tool_executed' AND properties.mcp_server_name='{SERVER}' AND ({INTERNAL}) AND ({NONFUZZ}) GROUP BY harness ORDER BY calls DESC LIMIT 20""",

    "Q4_errors": f"""SELECT properties.error_category AS cat, substring(properties.error_message,1,90) AS err, count() AS n, uniq(distinct_id) AS installs FROM events WHERE timestamp >= {DAY}-toIntervalDay(7) AND event='tool_executed' AND properties.error_category IS NOT NULL AND properties.mcp_server_name='{SERVER}' AND ({INTERNAL}) AND ({NONFUZZ}) GROUP BY cat, err ORDER BY n DESC LIMIT 15""",

    "Q5_geo": f"""SELECT upper(coalesce(nullIf(properties.geo_country,''),'??')) AS country, uniq(distinct_id) AS installs, count() AS calls FROM events WHERE timestamp >= {DAY}-toIntervalDay(1) AND timestamp < {DAY} AND event='tool_executed' AND properties.mcp_server_name='{SERVER}' AND ({INTERNAL}) AND ({NONFUZZ}) GROUP BY country ORDER BY calls DESC LIMIT 15""",

    "Q6_skills": f"""SELECT properties.tool_name AS tool, count() AS n FROM events WHERE timestamp >= {DAY}-toIntervalDay(7) AND event='tool_executed' AND properties.tool_name IN ('skills_list','skill_read','skill_tip_shown') AND properties.mcp_server_name='{SERVER}' AND ({INTERNAL}) AND ({NONFUZZ}) GROUP BY tool ORDER BY n DESC""",

    "Q7_session": f"""SELECT countIf(event='session_end' AND properties.tool_sequence>0) AS sessions_with_usage, avgIf(properties.tool_sequence, event='session_end' AND properties.tool_sequence>0) AS avg_calls_per_session, avgIf(properties.session_duration_s, event='session_end') AS avg_session_s FROM events WHERE timestamp >= {DAY}-toIntervalDay(1) AND timestamp < {DAY} AND properties.mcp_server_name='{SERVER}' AND ({INTERNAL}) AND ({NONFUZZ})""",
}

results = {}
for key, query_sql in queries.items():
    req = urllib.request.Request(POSTHOG_URL, data=json.dumps({"query": {"kind": "HogQLQuery", "query": query_sql}}).encode('utf-8'), headers=POSTHOG_HEADERS)
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            results[key] = {
                "columns": data.get("columns"),
                "results": data.get("results")
            }
    except Exception as e:
        results[key] = {"error": str(e)}

print(json.dumps(results, indent=2))
