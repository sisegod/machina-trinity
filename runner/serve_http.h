#pragma once

// HTTP helper functions extracted from cmd_serve.cpp

#ifndef _WIN32

#include <chrono>
#include <cstdint>
#include <sstream>
#include <string>
#include <unordered_map>

#include <csignal>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>

#include "machina/hash.h"
#include "machina/crypto.h"

namespace machina {

// Set socket recv/send timeouts for Slowloris defense
inline void set_socket_timeouts(int fd, int timeout_sec = 10) {
    struct timeval tv;
    tv.tv_sec = timeout_sec;
    tv.tv_usec = 0;
    ::setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    ::setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
}

// max_body: maximum body size in bytes. Content-Length exceeding this is rejected
// immediately without reading the body. 0 = use default 2MB cap.
inline bool read_http_request(int fd, std::string& head, std::string& body, size_t max_body = 0) {
    head.clear();
    body.clear();
    if (max_body == 0) max_body = 2 * 1024 * 1024; // default 2MB
    std::string buf;
    buf.resize(8192);
    std::string all;

    while (all.find("\r\n\r\n") == std::string::npos) {
        ssize_t n = ::recv(fd, buf.data(), buf.size(), 0);
        if (n <= 0) return false; // timeout or disconnect
        all.append(buf.data(), (size_t)n);
        if (all.size() > 1024 * 1024) return false; // header cap 1MB
    }

    size_t p = all.find("\r\n\r\n");
    head = all.substr(0, p + 4);
    std::string rest = all.substr(p + 4);

    size_t cl = 0;
    {
        int cl_count = 0;
        std::istringstream iss(head);
        std::string line;
        while (std::getline(iss, line)) {
            if (!line.empty() && line.back() == '\r') line.pop_back();
            std::string low = line;
            for (char& c : low) if (c >= 'A' && c <= 'Z') c = (char)(c - 'A' + 'a');
            if (low.rfind("content-length:", 0) == 0) {
                cl_count++;
                if (cl_count > 1) return false; // duplicate Content-Length: request smuggling
                std::string v = line.substr(15);
                while (!v.empty() && (v[0] == ' ' || v[0] == '\t')) v.erase(0, 1);
                try { cl = (size_t)std::stoull(v); } catch (...) { cl = 0; }
            }
        }
    }

    // Early reject: Content-Length exceeds configured max — don't read the body
    if (cl > max_body) return false;

    body = rest;
    while (body.size() < cl) {
        ssize_t n = ::recv(fd, buf.data(), buf.size(), 0);
        if (n <= 0) return false;
        body.append(buf.data(), (size_t)n);
        if (body.size() > max_body) return false; // safety net for chunked/missing CL
    }
    if (body.size() > cl) body.resize(cl);
    return true;
}

inline void send_json(int fd, int code, const std::string& json) {
    std::ostringstream oss;
    oss << "HTTP/1.1 " << code << " " << (code == 200 ? "OK" : "ERR") << "\r\n";
    oss << "Content-Type: application/json\r\n";
    oss << "Content-Length: " << json.size() << "\r\n";
    oss << "Connection: close\r\n\r\n";
    oss << json;
    auto s = oss.str();
    size_t sent = 0;
    while (sent < s.size()) {
        ssize_t n = ::send(fd, s.data() + sent, s.size() - sent, MSG_NOSIGNAL);
        if (n <= 0) break;
        sent += (size_t)n;
    }
}

inline std::string header_value_ci(const std::string& head, const std::string& key_lower) {
    std::istringstream iss(head);
    std::string line;
    std::getline(iss, line);
    while (std::getline(iss, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        auto c = line.find(':');
        if (c == std::string::npos) continue;
        std::string k = line.substr(0, c);
        for (char& ch : k) if (ch >= 'A' && ch <= 'Z') ch = (char)(ch - 'A' + 'a');
        if (k == key_lower) {
            std::string v = line.substr(c + 1);
            while (!v.empty() && (v[0] == ' ' || v[0] == '\t')) v.erase(0, 1);
            return v;
        }
    }
    return "";
}

inline bool api_token_ok(const std::string& head, const std::string& expected_token) {
    if (expected_token.empty()) return true;
    std::string x = header_value_ci(head, "x-api-token");
    if (!x.empty() && machina::constant_time_eq(x, expected_token)) return true;
    std::string auth = header_value_ci(head, "authorization");
    const std::string pfx = "Bearer ";
    if (auth.rfind(pfx, 0) == 0) {
        std::string t = auth.substr(pfx.size());
        return machina::constant_time_eq(t, expected_token);
    }
    return false;
}

struct TokenBucket {
    double tokens{0.0};
    double rate_per_sec{0.0};
    double capacity{0.0};
    int64_t last_ms{0};

    void init(int rpm, int64_t now_ms) {
        if (rpm <= 0) { rate_per_sec = 0.0; capacity = 0.0; tokens = 0.0; last_ms = now_ms; return; }
        rate_per_sec = (rpm / 60.0);
        capacity = (double)rpm;
        tokens = capacity;
        last_ms = now_ms;
    }

    bool allow(int cost, int64_t now_ms) {
        if (rate_per_sec <= 0.0 || capacity <= 0.0) return true;
        double dt = static_cast<double>(now_ms - last_ms) / 1000.0;
        if (dt > 0) {
            tokens = std::min(capacity, tokens + dt * rate_per_sec);
            last_ms = now_ms;
        }
        if (tokens >= cost) {
            tokens -= cost;
            return true;
        }
        return false;
    }
};

inline int64_t now_ms_wall() {
    return (int64_t)std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}

inline bool api_hmac_ok(const std::string& head,
                        const std::string& method,
                        const std::string& path,
                        const std::string& body,
                        const std::string& secret,
                        int ttl_sec,
                        std::unordered_map<std::string, int64_t>& nonce_cache) {
    if (secret.empty()) return true;
    if (ttl_sec <= 0) ttl_sec = 60;

    std::string ts_s = header_value_ci(head, "x-machina-ts");
    std::string nonce = header_value_ci(head, "x-machina-nonce");
    std::string sig = header_value_ci(head, "x-machina-signature");
    if (ts_s.empty() || nonce.empty() || sig.empty()) return false;

    long long ts = 0;
    try { ts = std::stoll(ts_s); } catch (...) { return false; }

    long long now = (long long)std::chrono::duration_cast<std::chrono::seconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    long long tsec = ts;
    if (ts > 20000000000LL) tsec = ts / 1000;
    if (tsec < 0) return false;

    long long skew = (tsec > now) ? (tsec - now) : (now - tsec);
    if (skew > ttl_sec) return false;

    const int64_t now_wall = now_ms_wall();
    auto it = nonce_cache.find(nonce);
    if (it != nonce_cache.end()) {
        if (now_wall - it->second < (int64_t)ttl_sec * 1000) return false;
    }
    nonce_cache[nonce] = now_wall;

    if (nonce_cache.size() > 5000) {
        // TTL-based pruning first
        for (auto iter = nonce_cache.begin(); iter != nonce_cache.end(); ) {
            if (now_wall - iter->second > (int64_t)ttl_sec * 1000) iter = nonce_cache.erase(iter);
            else ++iter;
        }
        // Hard cap: evict oldest entries (not random begin()) to prevent replay
        while (nonce_cache.size() > 10000) {
            auto oldest = nonce_cache.begin();
            for (auto jt = nonce_cache.begin(); jt != nonce_cache.end(); ++jt) {
                if (jt->second < oldest->second) oldest = jt;
            }
            nonce_cache.erase(oldest);
        }
    }

    std::string body_hash = machina::sha256_hex(body);
    std::string canon = ts_s + "\n" + nonce + "\n" + method + "\n" + path + "\n" + body_hash + "\n";
    std::string expected = machina::hmac_sha256_hex(secret, canon);

    std::string got = sig;
    if (got.rfind("v1=", 0) == 0) got = got.substr(3);

    return machina::constant_time_eq(expected, got);
}

} // namespace machina

#endif // !_WIN32
