#include "machina/tools.h"
#include "machina/json_mini.h"
#include "machina/proc.h"

#include <algorithm>
#include <cstring>
#include <sstream>
#include <string>
#include <vector>

#ifndef _WIN32
#include <arpa/inet.h>
#include <netdb.h>
#include <sys/socket.h>
#endif

namespace {

static int getenv_int(const char* name, int defv) {
    if (const char* v = std::getenv(name)) {
        try { return std::stoi(v); } catch (...) { return defv; }
    }
    return defv;
}

static size_t getenv_size_t(const char* name, size_t defv) {
    if (const char* v = std::getenv(name)) {
        try { return (size_t)std::stoull(v); } catch (...) { return defv; }
    }
    return defv;
}

static std::string trim_ws(std::string s) {
    while (!s.empty() && (s.back()=='\n' || s.back()=='\r' || s.back()==' ' || s.back()=='\t')) s.pop_back();
    size_t i=0;
    while (i<s.size() && (s[i]=='\n' || s[i]=='\r' || s[i]==' ' || s[i]=='\t')) i++;
    if (i) s.erase(0,i);
    return s;
}

static std::vector<std::string> split_csv(const std::string& s) {
    std::vector<std::string> out;
    std::string cur;
    for (char c : s) {
        if (c == ',') {
            if (!cur.empty()) out.push_back(trim_ws(cur));
            cur.clear();
        } else {
            cur.push_back(c);
        }
    }
    if (!cur.empty()) out.push_back(trim_ws(cur));
    out.erase(std::remove_if(out.begin(), out.end(), [](const std::string& x){ return x.empty(); }), out.end());
    return out;
}

static std::string lower_ascii(std::string s) {
    for (char& c : s) if (c>='A' && c<='Z') c = (char)(c - 'A' + 'a');
    return s;
}

static bool ends_with(const std::string& s, const std::string& suf) {
    if (s.size() < suf.size()) return false;
    return s.compare(s.size()-suf.size(), suf.size(), suf) == 0;
}

static std::string extract_host(const std::string& url) {
    // Minimal parser: scheme://host[:port]/...
    auto p = url.find("://");
    if (p == std::string::npos) return "";
    auto rest = url.substr(p+3);
    // strip path
    auto slash = rest.find('/');
    if (slash != std::string::npos) rest = rest.substr(0, slash);
    // strip userinfo
    auto at = rest.find('@');
    if (at != std::string::npos) rest = rest.substr(at+1);
    // strip port
    auto colon = rest.find(':');
    if (colon != std::string::npos) rest = rest.substr(0, colon);
    return rest;
}

static std::string extract_port(const std::string& url) {
    bool is_https = url.rfind("https://", 0) == 0;
    auto p = url.find("://");
    if (p == std::string::npos) return is_https ? "443" : "80";
    auto rest = url.substr(p+3);
    auto slash = rest.find('/');
    if (slash != std::string::npos) rest = rest.substr(0, slash);
    auto at = rest.find('@');
    if (at != std::string::npos) rest = rest.substr(at+1);
    auto colon = rest.find(':');
    if (colon != std::string::npos) return rest.substr(colon+1);
    return is_https ? "443" : "80";
}

static bool host_allowed(const std::string& host) {
    const char* env = std::getenv("MACHINA_HTTP_ALLOWED_HOSTS");
    if (!env || std::string(env).empty()) {
        // Default deny when MACHINA_HTTP_DEFAULT_DENY=1 (prod profile)
        const char* deny = std::getenv("MACHINA_HTTP_DEFAULT_DENY");
        if (deny && std::string(deny) == "1") return false;
        return true; // dev: default allow
    }

    std::string h = lower_ascii(host);
    for (auto tok : split_csv(env)) {
        tok = lower_ascii(tok);
        if (tok == "*") return true;
        if (tok.rfind("*.", 0) == 0) {
            std::string suf = tok.substr(1); // ".example.com"
            if (ends_with(h, suf)) return true;
            continue;
        }
        if (h == tok) return true;
    }
    return false;
}

// SSRF defense: block private/reserved/metadata IPs after DNS resolution.
// Covers RFC 1918, RFC 5737, RFC 6598, link-local, loopback, cloud metadata.
static bool is_private_or_reserved_ip(const std::string& ip) {
    // IPv4 checks
    struct in_addr addr4;
    if (inet_pton(AF_INET, ip.c_str(), &addr4) == 1) {
        uint32_t h = ntohl(addr4.s_addr);
        if ((h >> 24) == 127) return true;                        // 127.0.0.0/8 loopback
        if ((h >> 24) == 10)  return true;                        // 10.0.0.0/8
        if ((h >> 20) == (172 << 4 | 1)) return true;            // 172.16.0.0/12
        if ((h >> 16) == (192 << 8 | 168)) return true;          // 192.168.0.0/16
        if ((h >> 16) == (169 << 8 | 254)) return true;          // 169.254.0.0/16 link-local
        if ((h >> 22) == (100 << 2 | 1)) return true;            // 100.64.0.0/10 CGN (RFC 6598)
        if ((h >> 24) == 0)   return true;                        // 0.0.0.0/8
        if (h == 0xFFFFFFFF)  return true;                        // 255.255.255.255
        if ((h >> 8) == (192 << 16 | 0 << 8 | 0)) return true;  // 192.0.0.0/24
        if ((h >> 8) == (198 << 16 | 51 << 8 | 100)) return true; // 198.51.100.0/24 (RFC 5737)
        if ((h >> 8) == (203 << 16 | 0 << 8 | 113)) return true;  // 203.0.113.0/24 (RFC 5737)
        // AWS/GCP/Azure metadata: 169.254.169.254
        if (h == 0xA9FEA9FE) return true;
        return false;
    }
    // IPv6 checks
    struct in6_addr addr6;
    if (inet_pton(AF_INET6, ip.c_str(), &addr6) == 1) {
        if (IN6_IS_ADDR_LOOPBACK(&addr6)) return true;           // ::1
        if (IN6_IS_ADDR_LINKLOCAL(&addr6)) return true;          // fe80::/10
        if (IN6_IS_ADDR_SITELOCAL(&addr6)) return true;          // fec0::/10
        if (addr6.s6_addr[0] == 0xfc || addr6.s6_addr[0] == 0xfd) return true; // fc00::/7 ULA
        // ::ffff:mapped IPv4 — check inner address
        if (IN6_IS_ADDR_V4MAPPED(&addr6)) {
            uint32_t v4 = ntohl(*(uint32_t*)&addr6.s6_addr[12]);
            struct in_addr inner;
            inner.s_addr = htonl(v4);
            char buf[INET_ADDRSTRLEN];
            inet_ntop(AF_INET, &inner, buf, sizeof(buf));
            return is_private_or_reserved_ip(buf);
        }
    }
    return false;
}

// Result of SSRF check: error message (empty=safe) + first validated IP for --resolve pinning.
struct SsrfResult {
    std::string error;
    std::string resolved_ip;  // first safe IP — used with curl --resolve to prevent DNS rebinding
};

// Resolve hostname → check all IPs for SSRF. Returns validated IP on safe, error on block.
static SsrfResult ssrf_check_host(const std::string& host) {
#ifndef _WIN32
    // Block known metadata hostnames
    std::string lh = lower_ascii(host);
    if (lh == "metadata.google.internal" || lh == "metadata" ||
        lh.find("169.254.169.254") != std::string::npos) {
        return {"blocked: cloud metadata endpoint", ""};
    }

    struct addrinfo hints{}, *res = nullptr;
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    int err = getaddrinfo(host.c_str(), nullptr, &hints, &res);
    if (err != 0) return {"", ""};  // DNS failure → curl will handle
    std::string first_ip;
    for (auto* rp = res; rp; rp = rp->ai_next) {
        char ip[INET6_ADDRSTRLEN]{};
        if (rp->ai_family == AF_INET) {
            inet_ntop(AF_INET, &((struct sockaddr_in*)rp->ai_addr)->sin_addr, ip, sizeof(ip));
        } else if (rp->ai_family == AF_INET6) {
            inet_ntop(AF_INET6, &((struct sockaddr_in6*)rp->ai_addr)->sin6_addr, ip, sizeof(ip));
        }
        if (ip[0] && is_private_or_reserved_ip(ip)) {
            freeaddrinfo(res);
            return {std::string("blocked: resolves to private IP ") + ip, ""};
        }
        if (ip[0] && first_ip.empty()) first_ip = ip;
    }
    freeaddrinfo(res);
    return {"", first_ip};
#else
    (void)host;
    return {"", ""};
#endif
}

} // namespace

namespace machina {

// Tool: AID.NET.HTTP_GET.v1
ToolResult tool_http_get(const std::string& input_json, DSState& ds_tmp) {
#ifdef _WIN32
    (void)input_json; (void)ds_tmp;
    return {StepStatus::TOOL_ERROR, "{}", "http_get not supported on Windows in this build"};
#else
    auto url = json_mini::get_string(input_json, "url").value_or("");
    if (url.empty()) return {StepStatus::TOOL_ERROR, "{}", "missing url"};

    // Scheme allowlist
    if (!(url.rfind("http://", 0) == 0 || url.rfind("https://", 0) == 0)) {
        return {StepStatus::TOOL_ERROR, "{}", "only http/https allowed"};
    }

    // Host allowlist (optional — enforced when MACHINA_HTTP_ALLOWED_HOSTS is set)
    std::string host = extract_host(url);
    if (host.empty()) return {StepStatus::TOOL_ERROR, "{}", "cannot parse host"};
    if (!host_allowed(host)) return {StepStatus::TOOL_ERROR, "{}", "host not allowed: " + host};

    // SSRF defense: resolve hostname, block private/reserved IPs, pin resolved IP.
    auto ssrf = ssrf_check_host(host);
    if (!ssrf.error.empty()) {
        return {StepStatus::TOOL_ERROR, "{}", "SSRF blocked: " + ssrf.error};
    }

    ProcLimits lim;
    lim.timeout_ms = getenv_int("MACHINA_HTTP_TIMEOUT_MS", 3000);
    lim.stdout_max_bytes = getenv_size_t("MACHINA_HTTP_STDOUT_MAX", 128 * 1024);
    lim.rlimit_cpu_sec = getenv_int("MACHINA_HTTP_RLIMIT_CPU_SEC", 2);
    lim.rlimit_as_mb = getenv_size_t("MACHINA_HTTP_RLIMIT_AS_MB", 512);
    lim.rlimit_fsize_mb = getenv_size_t("MACHINA_HTTP_RLIMIT_FSIZE_MB", 1);
    lim.rlimit_nofile = getenv_int("MACHINA_HTTP_RLIMIT_NOFILE", 32);
    lim.rlimit_nproc = getenv_int("MACHINA_HTTP_RLIMIT_NPROC", 16);

    // curl with --resolve to pin DNS → prevents rebinding TOCTOU between our check and curl's resolution.
    std::vector<std::string> argv = {
        "curl",
        "-sS",
        "--max-time", "2",
        "--max-redirs", "0",
    };
    if (!ssrf.resolved_ip.empty()) {
        std::string port = extract_port(url);
        argv.push_back("--resolve");
        argv.push_back(host + ":" + port + ":" + ssrf.resolved_ip);
    }
    argv.push_back("--");
    argv.push_back(url);

    ProcResult pr;
    bool started = proc_run_capture_sandboxed(argv, ".", lim, &pr);
    if (!started) return {StepStatus::TOOL_ERROR, "{}", pr.error};

    Artifact a;
    a.type = "http_get";
    a.provenance = "http:get";
    a.size_bytes = pr.output.size();

    std::ostringstream payload;
    payload << "{";
    payload << "\"ok\":" << ((pr.exit_code == 0 && !pr.timed_out) ? "true" : "false") << ",";
    payload << "\"exit_code\":" << pr.exit_code << ",";
    payload << "\"timed_out\":" << (pr.timed_out ? "true" : "false") << ",";
    payload << "\"truncated\":" << (pr.output_truncated ? "true" : "false") << ",";
    payload << "\"host\":\"" << json_mini::json_escape(host) << "\",";
    payload << "\"url\":\"" << json_mini::json_escape(url) << "\",";
    payload << "\"body\":\"" << json_mini::json_escape(pr.output) << "\"";
    payload << "}";

    a.content_json = payload.str();
    ds_tmp.slots[(uint8_t)DSSlot::DS0] = a;

    return {StepStatus::OK, a.content_json, ""};
#endif
}

} // namespace machina
