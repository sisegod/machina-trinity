#include "cmd_serve.h"
#include "cmd_run.h"
#include "runner_utils.h"
#include "serve_http.h"
#include "tool_setup.h"

#include "machina/types.h"
#include "machina/json_mini.h"
#include "machina/cpq.h"
#include "machina/wal.h"

#include <atomic>
#include <iostream>
#include <mutex>
#include <queue>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#ifndef _WIN32

using namespace machina;

int cmd_serve(int argc, char** argv) {
    // Ignore SIGPIPE: writing to disconnected clients should not crash the server
    ::signal(SIGPIPE, SIG_IGN);

    const auto root = resolve_root(argv[0]);
    set_env_if_missing("MACHINA_TOOLHOST_BIN", (std::filesystem::path(argv[0]).parent_path() / "machina_toolhost").string());
    std::string host = "127.0.0.1";
    int port = 8080;
    std::filesystem::path q = default_queue_dir(root);
    int workers = 0;
    int scan_ms = runner_detail::getenv_int("MACHINA_SERVE_SCAN_MS", 150);

    for (int i = 2; i < argc; i++) {
        std::string a = argv[i];
        if (a == "--host" && i + 1 < argc) { host = argv[++i]; continue; }
        if (a == "--port" && i + 1 < argc) { port = std::atoi(argv[++i]); continue; }
        if (a == "--queue" && i + 1 < argc) { q = argv[++i]; continue; }
        if (a == "--workers" && i + 1 < argc) { workers = std::atoi(argv[++i]); if (workers < 0) workers = 0; if (workers > 64) workers = 64; continue; }
        if (a == "--scan_ms" && i + 1 < argc) { scan_ms = std::atoi(argv[++i]); if (scan_ms < 20) scan_ms = 20; if (scan_ms > 5000) scan_ms = 5000; continue; }
    }

    if (!q.is_absolute()) q = root / q;
    ensure_queue_dirs(q);

    auto inbox = q / "inbox";
    auto processing = q / "processing";
    auto retry = q / "retry";
    auto done = q / "done";
    auto failed = q / "failed";
    auto dlq = q / "dlq";
    auto out = q / "out";
    auto logs = root / "logs";

    machina::ConcurrentPriorityQueue<std::filesystem::path> memq;
    std::unordered_set<std::string> memq_seen;
    std::mutex memq_mu;
    std::atomic<bool> stop_workers{false};
    std::atomic<uint64_t> jobs_processed{0}, jobs_ok{0}, jobs_fail{0};

    // Per-tool metrics: aid → {ok_count, fail_count, total_duration_ms}
    std::mutex tool_metrics_mu;
    struct ToolMetric { uint64_t ok{0}; uint64_t fail{0}; uint64_t duration_ms{0}; };
    std::unordered_map<std::string, ToolMetric> tool_metrics;

    const bool wal_enable = (runner_detail::getenv_int("MACHINA_WAL_ENABLE", 1) != 0);
    const int wal_checkpoint_ms = runner_detail::getenv_int("MACHINA_WAL_CHECKPOINT_MS", 5000);
    const bool wal_fsync = (runner_detail::getenv_int("MACHINA_WAL_FSYNC", 0) != 0);

    std::unique_ptr<machina::Wal> wal;
    std::filesystem::path wal_dir = q / "wal";
    std::filesystem::path wal_file = wal_dir / "queue.wal.jsonl";
    std::filesystem::path ckpt_file = wal_dir / "checkpoint.json";

    struct DelayedItem { int64_t due_ms; std::filesystem::path path; std::string rest; };
    struct DelayedCmp { bool operator()(const DelayedItem& a, const DelayedItem& b) const { return a.due_ms > b.due_ms; } };
    std::priority_queue<DelayedItem, std::vector<DelayedItem>, DelayedCmp> delayq;
    std::unordered_set<std::string> delay_seen;
    std::mutex delay_mu;

    // Dedup cache for /enqueue idempotency (keyed by request_id)
    std::unordered_map<std::string, int64_t> dedup_cache;
    std::mutex dedup_mu;
    const int64_t dedup_ttl_ms = getenv_i64("MACHINA_DEDUP_TTL_MS", 300000); // 5 min default

    // Create server socket BEFORE starting worker threads, so that failures here
    // don't leave running threads dangling (which would std::terminate on destruction).
    int sfd = ::socket(AF_INET, SOCK_STREAM, 0);
    if (sfd < 0) { std::cerr << "socket failed\n"; return 2; }
    {
        int one = 1;
        ::setsockopt(sfd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    }

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons((uint16_t)port);
    if (::inet_pton(AF_INET, host.c_str(), &addr.sin_addr) <= 0) {
        std::cerr << "bad host\n";
        ::close(sfd);
        return 2;
    }

    if (::bind(sfd, (sockaddr*)&addr, sizeof(addr)) < 0) {
        std::cerr << "bind failed\n";
        ::close(sfd);
        return 2;
    }

    if (::listen(sfd, 64) < 0) {
        std::cerr << "listen failed\n";
        ::close(sfd);
        return 2;
    }

    auto fname_priority = [&](const std::string& fname) -> int {
        if (fname.size() >= 5 && fname[0] == 'p') {
            int v = 0;
            for (int i = 1; i <= 4; i++) {
                char c = fname[i];
                if (c < '0' || c > '9') return 5000;
                v = v * 10 + (c - '0');
            }
            return v;
        }
        return 5000;
    };

    auto memq_try_enqueue_path = [&](const std::filesystem::path& p) {
        if (workers <= 0) return;
        std::string key = p.filename().string();
        {
            std::lock_guard<std::mutex> lk(memq_mu);
            if (memq_seen.find(key) != memq_seen.end()) return;
            memq_seen.insert(key);
        }
        memq.push(fname_priority(key), p);
    };

    auto memq_drop_seen = [&](const std::string& key) {
        std::lock_guard<std::mutex> lk(memq_mu);
        memq_seen.erase(key);
    };

    auto wal_emit = [&](const std::string& t, const std::string& extra_fields_json) {
        if (!(workers > 0 && wal_enable && wal)) return;
        std::ostringstream j;
        j << "{";
        j << "\"t\":\"" << json_escape(t) << "\"";
        j << ",\"ms\":" << now_ms_i64();
        j << extra_fields_json;
        j << "}";
        (void)wal->append_json_line(j.str());
    };

    auto delayq_schedule = [&](int64_t due_ms, const std::filesystem::path& pth, const std::string& rest) {
        if (due_ms <= 0) return;
        std::string key = pth.filename().string();
        {
            std::lock_guard<std::mutex> lk(delay_mu);
            if (delay_seen.find(key) != delay_seen.end()) return;
            delay_seen.insert(key);
            delayq.push(DelayedItem{due_ms, pth, rest});
        }
        wal_emit("SCHED", std::string(",\"due_ms\":") + std::to_string(due_ms)
                        + ",\"name\":\"" + json_escape(key) + "\"");
    };

    auto write_checkpoint = [&]() {
        if (!(workers > 0 && wal_enable)) return;
        std::vector<std::string> inbox_names;
        std::vector<std::pair<int64_t, std::string>> retry_items;
        {
            std::lock_guard<std::mutex> lk(memq_mu);
            inbox_names.reserve(memq_seen.size());
            for (auto& k : memq_seen) inbox_names.push_back(k);
        }
        {
            std::lock_guard<std::mutex> lk(delay_mu);
            auto tmp = delayq;
            while (!tmp.empty()) {
                auto it = tmp.top();
                tmp.pop();
                retry_items.push_back({it.due_ms, it.path.filename().string()});
            }
        }

        std::ostringstream j;
        j << "{";
        j << "\"version\":1";
        j << ",\"created_ms\":" << now_ms_i64();
        j << ",\"inbox\":[";
        for (size_t i = 0; i < inbox_names.size(); i++) {
            if (i) j << ",";
            j << "\"" << json_escape(inbox_names[i]) << "\"";
        }
        j << "]";
        j << ",\"retry\":[";
        for (size_t i = 0; i < retry_items.size(); i++) {
            if (i) j << ",";
            j << "{";
            j << "\"due_ms\":" << retry_items[i].first << ",";
            j << "\"name\":\"" << json_escape(retry_items[i].second) << "\"";
            j << "}";
        }
        j << "]";
        j << "}";

        std::string err = write_atomic_json(ckpt_file, j.str());
        if (!err.empty()) {
            std::cerr << "[serve] checkpoint write failed: " << err << "\n";
            return;
        }

        if (wal) {
            std::string terr = wal->truncate();
            if (!terr.empty()) std::cerr << "[serve] wal truncate failed: " << terr << "\n";
        }
    };

    auto load_checkpoint = [&](std::vector<std::string>& inbox_names, std::vector<std::pair<int64_t, std::string>>& retry_items) -> bool {
        std::error_code ec;
        if (!std::filesystem::exists(ckpt_file, ec)) return false;
        std::string s = slurp_file(ckpt_file);
        if (s.empty()) return false;
        auto ver = machina::json_mini::get_int(s, "version").value_or(0);
        if (ver != 1) return false;
        inbox_names = machina::json_mini::get_array_strings(s, "inbox");
        if (auto raw = machina::json_mini::get_array_raw(s, "retry")) {
            auto objs = machina::json_mini::parse_array_objects_raw(*raw);
            for (auto& o : objs) {
                auto due_val = machina::json_mini::get_int(o, "due_ms").value_or(0);
                auto name = machina::json_mini::get_string(o, "name").value_or("");
                if (!name.empty() && due_val > 0) retry_items.push_back({due_val, name});
            }
        }
        return true;
    };

    auto process_processing_file = [&](const std::filesystem::path& proc, const std::string& base, int wid) {
        // Delegate to shared job processing (result metadata + file moves handled there)
        JobResult jr = process_queue_job(proc, base, argv[0], root, q);

        // serve-specific: WAL event + counters
        {
            std::string event = (jr.exit_code == 0) ? "DONE" : (jr.scheduled_retry ? "RETRY" : (jr.deadletter ? "DLQ" : "FAIL"));
            wal_emit(event,
                     std::string(",\"src\":\"") + json_escape(proc.filename().string())
                     + "\",\"dst\":\"" + json_escape(jr.final_path.filename().string())
                     + "\",\"rc\":" + std::to_string(jr.exit_code)
                     + ",\"attempt\":" + std::to_string(jr.attempt)
                     + ",\"worker\":" + std::to_string(wid));
        }

        jobs_processed.fetch_add(1);
        if (jr.exit_code == 0) jobs_ok.fetch_add(1); else jobs_fail.fetch_add(1);

        // Aggregate per-tool metrics from run log
        if (!jr.tool_metrics.empty()) {
            std::lock_guard<std::mutex> lk(tool_metrics_mu);
            for (const auto& tm : jr.tool_metrics) {
                auto& m = tool_metrics[tm.aid];
                if (tm.ok) m.ok++; else m.fail++;
                m.duration_ms += (uint64_t)tm.duration_ms;
            }
        }
    };

    std::vector<std::thread> worker_threads;
    std::unique_ptr<std::thread> feeder_thread;
    std::unique_ptr<std::thread> checkpoint_thread;
    if (workers > 0) {
        std::cerr << "[serve] integrated daemon mode: workers=" << workers << " scan_ms=" << scan_ms << "\n";

        if (wal_enable) {
            std::error_code ec;
            std::filesystem::create_directories(wal_dir, ec);
            wal.reset(new machina::Wal(wal_file));
            wal->set_fsync(wal_fsync);
            std::string werr = wal->open(false);
            if (!werr.empty()) {
                std::cerr << "[serve] wal open failed: " << werr << "\n";
            }
        }

        // Crash recovery
        {
            std::error_code ec;
            for (auto& de : std::filesystem::directory_iterator(processing, ec)) {
                if (ec) break;
                if (!de.is_regular_file()) continue;
                auto fn = de.path().filename().string();
                if (!ends_with(fn, ".processing")) continue;
                std::string rest = fn;
                if (ends_with(rest, ".processing")) rest.resize(rest.size() - std::string(".processing").size());
                std::filesystem::path dst = inbox / rest;
                std::error_code ec2;
                std::filesystem::rename(de.path(), dst, ec2);
                if (!ec2) {
                    wal_emit("RECOVER", std::string(",\"from\":\"") + json_escape(fn) + "\",\"to\":\"" + json_escape(dst.filename().string()) + "\"");
                    memq_try_enqueue_path(dst);
                }
            }
        }

        // Load checkpoint
        {
            std::vector<std::string> ck_inbox;
            std::vector<std::pair<int64_t, std::string>> ck_retry;
            bool ok = load_checkpoint(ck_inbox, ck_retry);
            if (ok) {
                for (auto& name : ck_inbox) {
                    if (name.empty()) continue;
                    memq_try_enqueue_path(inbox / name);
                }
                for (auto& it : ck_retry) {
                    int64_t due_val = it.first;
                    std::string name = it.second;
                    int64_t due2 = 0;
                    std::string rest;
                    if (!parse_retry_name(name, due2, rest)) continue;
                    if (due2 > 0) due_val = due2;
                    delayq_schedule(due_val, retry / name, rest);
                }
            }

            auto jobs_list = list_inbox_json(inbox);
            for (auto& pth : jobs_list) memq_try_enqueue_path(pth);

            std::error_code ec;
            for (auto& de : std::filesystem::directory_iterator(retry, ec)) {
                if (ec) break;
                if (!de.is_regular_file()) continue;
                auto fn = de.path().filename().string();
                int64_t due_val = 0;
                std::string rest;
                if (!parse_retry_name(fn, due_val, rest)) continue;
                delayq_schedule(due_val, de.path(), rest);
            }
        }

        // Replay WAL
        auto replay_wal = [&]() {
            if (!wal_enable) return;
            std::ifstream in(wal_file);
            if (!in.good()) return;
            std::string line;
            while (std::getline(in, line)) {
                if (line.empty()) continue;
                auto t = machina::json_mini::get_string(line, "t").value_or("");
                if (t == "ENQ") {
                    auto name = machina::json_mini::get_string(line, "name").value_or("");
                    if (!name.empty()) memq_try_enqueue_path(inbox / name);
                } else if (t == "SCHED") {
                    auto name = machina::json_mini::get_string(line, "name").value_or("");
                    auto due_val = machina::json_mini::get_int(line, "due_ms").value_or(0);
                    if (name.empty() || due_val <= 0) continue;
                    int64_t due2 = 0;
                    std::string rest;
                    (void)parse_retry_name(name, due2, rest);
                    if (!rest.empty()) delayq_schedule((int64_t)due_val, retry / name, rest);
                } else if (t == "PROMOTE" || t == "RECOVER") {
                    auto to = machina::json_mini::get_string(line, "to").value_or("");
                    if (!to.empty()) memq_try_enqueue_path(inbox / to);
                } else if (t == "DEDUP") {
                    // Restore dedup cache from WAL
                    auto rid = machina::json_mini::get_string(line, "request_id").value_or("");
                    auto ts_val = machina::json_mini::get_int(line, "ms").value_or(0);
                    if (!rid.empty() && ts_val > 0) {
                        const int64_t now_w = now_ms_wall();
                        // Only restore if still within TTL
                        if ((now_w - ts_val) < dedup_ttl_ms) {
                            std::lock_guard<std::mutex> lk(dedup_mu);
                            dedup_cache[rid] = ts_val;
                        }
                    }
                }
            }
        };
        replay_wal();

        write_checkpoint();

        feeder_thread.reset(new std::thread([&]() {
            int64_t last_scan = 0;
            while (!stop_workers.load()) {
                int64_t now = now_ms_i64();

                std::vector<DelayedItem> promote;
                {
                    std::lock_guard<std::mutex> lk(delay_mu);
                    while (!delayq.empty() && delayq.top().due_ms <= now) {
                        promote.push_back(delayq.top());
                        delay_seen.erase(delayq.top().path.filename().string());
                        delayq.pop();
                    }
                }
                for (auto& it : promote) {
                    std::string rest = it.rest;
                    if (rest.empty()) {
                        int64_t dummy = 0;
                        parse_retry_name(it.path.filename().string(), dummy, rest);
                    }
                    if (rest.empty()) continue;
                    std::filesystem::path dst = inbox / rest;
                    std::error_code ec;
                    std::filesystem::rename(it.path, dst, ec);
                    if (ec) continue;
                    wal_emit("PROMOTE", std::string(",\"from\":\"") + json_escape(it.path.filename().string()) + "\",\"to\":\"" + json_escape(dst.filename().string()) + "\"");
                    memq_try_enqueue_path(dst);
                }

                if (now - last_scan >= scan_ms) {
                    last_scan = now;
                    auto jobs_list = list_inbox_json(inbox);
                    for (auto& pth : jobs_list) memq_try_enqueue_path(pth);

                    std::error_code ec;
                    for (auto& de : std::filesystem::directory_iterator(retry, ec)) {
                        if (ec) break;
                        if (!de.is_regular_file()) continue;
                        auto fn = de.path().filename().string();
                        int64_t due_val = 0;
                        std::string rest;
                        if (!parse_retry_name(fn, due_val, rest)) continue;
                        delayq_schedule(due_val, de.path(), rest);
                    }
                }

                int64_t sleep_for_ms = scan_ms;
                {
                    std::lock_guard<std::mutex> lk(delay_mu);
                    if (!delayq.empty()) {
                        int64_t d = delayq.top().due_ms - now;
                        if (d < 0) d = 0;
                        if (d < sleep_for_ms) sleep_for_ms = d;
                    }
                }
                if (sleep_for_ms < 10) sleep_for_ms = 10;
                sleep_ms((int)sleep_for_ms);
            }
        }));

        if (wal_enable && wal_checkpoint_ms > 0) {
            checkpoint_thread.reset(new std::thread([&]() {
                while (!stop_workers.load()) {
                    sleep_ms(wal_checkpoint_ms);
                    if (stop_workers.load()) break;
                    write_checkpoint();
                }
            }));
        }

        worker_threads.reserve((size_t)workers);
        for (int wid = 0; wid < workers; wid++) {
            worker_threads.emplace_back([&, wid]() {
                machina::ConcurrentPriorityQueue<std::filesystem::path>::Item qi;
                while (memq.pop(qi)) {
                    std::filesystem::path p = qi.value;
                    if (stop_workers.load()) break;
                    std::string base = p.filename().string();
                    std::filesystem::path proc = processing / (base + ".processing");
                    std::error_code ec;
                    std::filesystem::rename(p, proc, ec);
                    if (ec) {
                        memq_drop_seen(base);
                        sleep_ms(5);
                        continue;
                    }
                    memq_drop_seen(base);
                    process_processing_file(proc, base, wid);
                }
            });
        }
    }

    std::string api_token;
    if (const char* e = std::getenv("MACHINA_API_TOKEN")) api_token = e;

    std::string hmac_secret;
    if (const char* e = std::getenv("MACHINA_API_HMAC_SECRET")) hmac_secret = e;

    // Fail-closed warning: no auth configured
    if (api_token.empty() && hmac_secret.empty()) {
        std::cerr << "[WARN] MACHINA_API_TOKEN and MACHINA_API_HMAC_SECRET are both unset.\n";
        std::cerr << "[WARN] /shutdown, /enqueue, /run_sync will reject all requests (fail-closed).\n";
        std::cerr << "[WARN] Set MACHINA_API_TOKEN or MACHINA_API_HMAC_SECRET to enable authenticated access.\n";
    }
    int hmac_ttl_sec = 60;
    if (const char* e = std::getenv("MACHINA_API_HMAC_TTL_SEC")) { try { hmac_ttl_sec = std::stoi(e); } catch (...) {} }
    if (hmac_ttl_sec <= 0) hmac_ttl_sec = 60;

    size_t max_body_bytes = 2 * 1024 * 1024;
    if (const char* e = std::getenv("MACHINA_API_MAX_BODY_BYTES")) { try { max_body_bytes = (size_t)std::stoull(e); } catch (...) {} }

    int rpm_global = -1;
    if (const char* e = std::getenv("MACHINA_API_RPM")) { try { rpm_global = std::stoi(e); } catch (...) {} }
    int rpm_enqueue = rpm_global;
    int rpm_run = rpm_global;
    if (const char* e = std::getenv("MACHINA_API_ENQUEUE_RPM")) { try { rpm_enqueue = std::stoi(e); } catch (...) {} }
    if (const char* e = std::getenv("MACHINA_API_RUNSYNC_RPM")) { try { rpm_run = std::stoi(e); } catch (...) {} }

    TokenBucket tb_enqueue, tb_run;
    tb_enqueue.init(rpm_enqueue, now_ms_wall());
    tb_run.init(rpm_run, now_ms_wall());

    std::unordered_map<std::string, int64_t> nonce_cache;
    std::mutex http_mu; // protects nonce_cache, tb_enqueue, tb_run
    constexpr int max_http_conns = 32;
    std::atomic<int> active_conns{0};
    std::vector<std::thread> http_threads;
    std::mutex http_threads_mu;
    std::string argv0_copy = argv[0]; // copy before threads can outlive stack

    std::cerr << "[serve] http://" << host << ":" << port << " queue=" << q;
    if (workers > 0) std::cerr << " (integrated workers=" << workers << ")";
    std::cerr << "\n";

    std::atomic<bool> running{true};
    while (running.load()) {
        sockaddr_in caddr{}; socklen_t clen = sizeof(caddr);
        int cfd = ::accept(sfd, (sockaddr*)&caddr, &clen);
        if (cfd < 0) continue;
        if (active_conns.load() >= max_http_conns) {
            send_json(cfd, 503, "{\"ok\":false,\"error\":\"too many connections\"}");
            ::close(cfd);
            continue;
        }
        active_conns.fetch_add(1);
        set_socket_timeouts(cfd, 10); // 10s per-connection timeout (Slowloris defense)

        std::thread t([&, cfd, caddr]() {
        struct ConnGuard { std::atomic<int>& c; ~ConnGuard() { c.fetch_sub(1); } } cg{active_conns};

        std::string head, body;
        if (!read_http_request(cfd, head, body, max_body_bytes)) { ::close(cfd); return; }

        std::istringstream iss(head);
        std::string method, path, ver;
        iss >> method >> path >> ver;
        char ipbuf[64] = {0};
        (void)inet_ntop(AF_INET, &caddr.sin_addr, ipbuf, sizeof(ipbuf));

        if (method == "GET" && path == "/health") {
            send_json(cfd, 200, "{\"ok\":true}");
            ::close(cfd);
            return;
        }

        if (method == "POST" && path == "/shutdown") {
            // Fail-closed: if no auth is configured, /shutdown is disabled entirely
            if (api_token.empty() && hmac_secret.empty()) {
                send_json(cfd, 403, "{\"ok\":false,\"error\":\"shutdown disabled: no auth configured\"}");
                ::close(cfd);
                return;
            }
            {
                bool auth_ok;
                {
                    std::lock_guard<std::mutex> lk(http_mu);
                    auth_ok = api_token_ok(head, api_token) && api_hmac_ok(head, method, path, body, hmac_secret, hmac_ttl_sec, nonce_cache);
                }
                if (!auth_ok) {
                    send_json(cfd, 401, "{\"ok\":false,\"error\":\"unauthorized\"}");
                    ::close(cfd);
                    return;
                }
            }
            send_json(cfd, 200, "{\"ok\":true,\"message\":\"shutting_down\"}");
            ::close(cfd);
            running.store(false);
            return;
        }

        if (method == "GET" && path == "/stats") {
            auto count = [&](const std::filesystem::path& p) {
                size_t n = 0; std::error_code ec;
                if (!std::filesystem::exists(p, ec)) return n;
                for (auto& e : std::filesystem::directory_iterator(p, ec)) {
                    if (ec) break;
                    if (e.is_regular_file(ec)) n++;
                }
                return n;
            };

            std::ostringstream j;
            j << "{";
            j << "\"inbox\":" << count(q / "inbox") << ",";
            j << "\"processing\":" << count(q / "processing") << ",";
            j << "\"retry\":" << count(q / "retry") << ",";
            j << "\"done\":" << count(q / "done") << ",";
            j << "\"failed\":" << count(q / "failed") << ",";
            j << "\"dlq\":" << count(q / "dlq") << ",";
            j << "\"workers\":" << workers << ",";
            j << "\"memq\":" << memq.size() << ",";
            j << "\"jobs_processed\":" << jobs_processed.load() << ",";
            j << "\"jobs_ok\":" << jobs_ok.load() << ",";
            j << "\"jobs_fail\":" << jobs_fail.load();
            j << "}";
            send_json(cfd, 200, j.str());
            ::close(cfd);
            return;
        }

        if (method == "GET" && path == "/metrics") {
            // Prometheus text exposition format
            auto count = [&](const std::filesystem::path& p) {
                size_t n = 0; std::error_code ec;
                if (!std::filesystem::exists(p, ec)) return n;
                for (auto& e : std::filesystem::directory_iterator(p, ec)) {
                    if (ec) break;
                    if (e.is_regular_file(ec)) n++;
                }
                return n;
            };

            std::ostringstream m;
            m << "# HELP machina_jobs_processed_total Total jobs processed\n";
            m << "# TYPE machina_jobs_processed_total counter\n";
            m << "machina_jobs_processed_total " << jobs_processed.load() << "\n";
            m << "# HELP machina_jobs_ok_total Successful jobs\n";
            m << "# TYPE machina_jobs_ok_total counter\n";
            m << "machina_jobs_ok_total " << jobs_ok.load() << "\n";
            m << "# HELP machina_jobs_fail_total Failed jobs\n";
            m << "# TYPE machina_jobs_fail_total counter\n";
            m << "machina_jobs_fail_total " << jobs_fail.load() << "\n";
            m << "# HELP machina_queue_inbox_size Current inbox queue depth\n";
            m << "# TYPE machina_queue_inbox_size gauge\n";
            m << "machina_queue_inbox_size " << count(q / "inbox") << "\n";
            m << "# HELP machina_queue_processing_size Jobs currently processing\n";
            m << "# TYPE machina_queue_processing_size gauge\n";
            m << "machina_queue_processing_size " << count(q / "processing") << "\n";
            m << "# HELP machina_queue_retry_size Jobs waiting for retry\n";
            m << "# TYPE machina_queue_retry_size gauge\n";
            m << "machina_queue_retry_size " << count(q / "retry") << "\n";
            m << "# HELP machina_queue_failed_size Total failed jobs\n";
            m << "# TYPE machina_queue_failed_size gauge\n";
            m << "machina_queue_failed_size " << count(q / "failed") << "\n";
            m << "# HELP machina_queue_dlq_size Dead letter queue depth\n";
            m << "# TYPE machina_queue_dlq_size gauge\n";
            m << "machina_queue_dlq_size " << count(q / "dlq") << "\n";
            m << "# HELP machina_memq_size In-memory queue depth\n";
            m << "# TYPE machina_memq_size gauge\n";
            m << "machina_memq_size " << memq.size() << "\n";
            m << "# HELP machina_workers_configured Number of worker threads\n";
            m << "# TYPE machina_workers_configured gauge\n";
            m << "machina_workers_configured " << workers << "\n";

            // Per-tool metrics
            {
                std::lock_guard<std::mutex> lk(tool_metrics_mu);
                if (!tool_metrics.empty()) {
                    m << "# HELP machina_tool_ok_total Successful executions per tool\n";
                    m << "# TYPE machina_tool_ok_total counter\n";
                    for (const auto& [aid, tm] : tool_metrics)
                        m << "machina_tool_ok_total{aid=\"" << aid << "\"} " << tm.ok << "\n";
                    m << "# HELP machina_tool_fail_total Failed executions per tool\n";
                    m << "# TYPE machina_tool_fail_total counter\n";
                    for (const auto& [aid, tm] : tool_metrics)
                        m << "machina_tool_fail_total{aid=\"" << aid << "\"} " << tm.fail << "\n";
                    m << "# HELP machina_tool_duration_ms_total Cumulative execution time per tool\n";
                    m << "# TYPE machina_tool_duration_ms_total counter\n";
                    for (const auto& [aid, tm] : tool_metrics)
                        m << "machina_tool_duration_ms_total{aid=\"" << aid << "\"} " << tm.duration_ms << "\n";
                }
            }

            std::string body_text = m.str();
            std::ostringstream resp;
            resp << "HTTP/1.1 200 OK\r\n";
            resp << "Content-Type: text/plain; version=0.0.4; charset=utf-8\r\n";
            resp << "Content-Length: " << body_text.size() << "\r\n\r\n";
            resp << body_text;
            std::string rs = resp.str();
            size_t ws = 0;
            while (ws < rs.size()) {
                ssize_t wn = ::send(cfd, rs.data() + ws, rs.size() - ws, MSG_NOSIGNAL);
                if (wn <= 0) break;
                ws += (size_t)wn;
            }
            ::close(cfd);
            return;
        }

        if (method == "POST" && path == "/enqueue") {
            // Fail-closed: no auth configured → reject
            if (api_token.empty() && hmac_secret.empty()) {
                send_json(cfd, 403, "{\"ok\":false,\"error\":\"enqueue disabled: no auth configured\"}");
                ::close(cfd);
                return;
            }
            {
                int reject_code = 0;
                std::string reject_msg;
                {
                    std::lock_guard<std::mutex> lk(http_mu);
                    if (!api_token_ok(head, api_token)) {
                        reject_code = 401; reject_msg = "{\"ok\":false,\"error\":\"unauthorized\"}";
                    } else if (!api_hmac_ok(head, method, path, body, hmac_secret, hmac_ttl_sec, nonce_cache)) {
                        reject_code = 401; reject_msg = "{\"ok\":false,\"error\":\"bad_signature\"}";
                    } else if (!tb_enqueue.allow(1, now_ms_wall())) {
                        reject_code = 429; reject_msg = "{\"ok\":false,\"error\":\"rate_limited\"}";
                    }
                }
                if (reject_code) {
                    send_json(cfd, reject_code, reject_msg);
                    ::close(cfd);
                    return;
                }
            }
            if (body.empty() || body.size() > max_body_bytes) {
                send_json(cfd, 400, "{\"ok\":false,\"error\":\"bad body\"}");
                ::close(cfd);
                return;
            }
            if (!machina::json_mini::has_key(body, "goal_id")) {
                send_json(cfd, 400, "{\"ok\":false,\"error\":\"missing goal_id\"}");
                ::close(cfd);
                return;
            }
            // Idempotency: deduplicate by optional request_id
            auto request_id = machina::json_mini::get_string(body, "request_id");
            if (request_id && !request_id->empty()) {
                const int64_t now_wall = now_ms_wall();
                bool is_dup = false;
                {
                    std::lock_guard<std::mutex> lk(dedup_mu);
                    auto it = dedup_cache.find(*request_id);
                    if (it != dedup_cache.end() && (now_wall - it->second) < dedup_ttl_ms) {
                        is_dup = true;
                    } else {
                        dedup_cache[*request_id] = now_wall;
                        // Periodic cleanup: prune expired entries
                        if (dedup_cache.size() > 5000) {
                            for (auto iter = dedup_cache.begin(); iter != dedup_cache.end(); ) {
                                if (now_wall - iter->second > dedup_ttl_ms) iter = dedup_cache.erase(iter);
                                else ++iter;
                            }
                            // Hard cap: if still too large after TTL prune, evict oldest
                            while (dedup_cache.size() > 10000) dedup_cache.erase(dedup_cache.begin());
                        }
                    }
                }
                if (is_dup) {
                    send_json(cfd, 200, "{\"ok\":true,\"deduplicated\":true}");
                    ::close(cfd);
                    return;
                }
                // Persist dedup entry to WAL for crash-recovery
                wal_emit("DEDUP", std::string(",\"request_id\":\"") + json_escape(*request_id) + "\"");
            }
            auto ts = (long long)std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::system_clock::now().time_since_epoch()).count();
            int priority = 5000;
            {
                auto qmeta = machina::json_mini::get_object_raw(body, "_queue").value_or("");
                if (!qmeta.empty()) {
                    if (auto v = machina::json_mini::get_int(qmeta, "priority")) priority = (int)*v;
                }
                if (priority < 0) priority = 0;
                if (priority > 9999) priority = 9999;
            }
            std::ostringstream fn;
            fn << "p";
            fn.width(4);
            fn.fill('0');
            fn << priority;
            fn << "_rq_" << ts << "_" << machina::secure_rand32();
            if (request_id && !request_id->empty()) {
                // Embed a short hash of request_id for traceability
                fn << "_rid" << (uint32_t)(machina::hash::fnv1a64(*request_id) & 0xFFFFFFFF);
            }
            fn << ".json";
            std::string fname = fn.str();
            auto dst = q / "inbox" / fname;
            std::string err = write_atomic_json(dst, body);
            if (!err.empty()) {
                send_json(cfd, 500, std::string("{\"ok\":false,\"error\":\"") + json_escape(err) + "\"}");
                ::close(cfd);
                return;
            }
            wal_emit("ENQ", std::string(",\"name\":\"") + json_escape(fname) + "\",\"prio\":" + std::to_string(priority)
                     + (request_id && !request_id->empty() ? std::string(",\"request_id\":\"") + json_escape(*request_id) + "\"" : ""));
            memq_try_enqueue_path(dst);
            send_json(cfd, 200, std::string("{\"ok\":true,\"queued\":\"") + json_escape(dst.filename().string()) + "\"}");
            ::close(cfd);
            return;
        }

        if (method == "POST" && path == "/run_sync") {
            // Fail-closed: no auth configured → reject
            if (api_token.empty() && hmac_secret.empty()) {
                send_json(cfd, 403, "{\"ok\":false,\"error\":\"run_sync disabled: no auth configured\"}");
                ::close(cfd);
                return;
            }
            {
                int reject_code = 0;
                std::string reject_msg;
                {
                    std::lock_guard<std::mutex> lk(http_mu);
                    if (!api_token_ok(head, api_token)) {
                        reject_code = 401; reject_msg = "{\"ok\":false,\"error\":\"unauthorized\"}";
                    } else if (!api_hmac_ok(head, method, path, body, hmac_secret, hmac_ttl_sec, nonce_cache)) {
                        reject_code = 401; reject_msg = "{\"ok\":false,\"error\":\"bad_signature\"}";
                    } else if (!tb_run.allow(1, now_ms_wall())) {
                        reject_code = 429; reject_msg = "{\"ok\":false,\"error\":\"rate_limited\"}";
                    }
                }
                if (reject_code) {
                    send_json(cfd, reject_code, reject_msg);
                    ::close(cfd);
                    return;
                }
            }
            if (body.empty() || body.size() > max_body_bytes) {
                send_json(cfd, 400, "{\"ok\":false,\"error\":\"bad body\"}");
                ::close(cfd);
                return;
            }
            if (!machina::json_mini::has_key(body, "goal_id")) {
                send_json(cfd, 400, "{\"ok\":false,\"error\":\"missing goal_id\"}");
                ::close(cfd);
                return;
            }

            auto ts = (long long)std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::system_clock::now().time_since_epoch()).count();
            std::string fname = "sync_" + std::to_string(ts) + "_" + std::to_string(machina::secure_rand32()) + ".json";
            auto tmp = q / "tmp" / fname;
            (void)write_atomic_json(tmp, body);

            auto sync_logs = root / "logs";
            auto before = list_run_logs(sync_logs);

            std::string tmp_path = tmp.string();
            std::string arg_run = "run";
            char* av[3];
            av[0] = argv0_copy.data();
            av[1] = arg_run.data();
            av[2] = tmp_path.data();
            int rc = cmd_run(3, av);

            auto newlog = newest_new_log(sync_logs, before);
            std::error_code ec;
            std::filesystem::remove(tmp, ec);

            std::string log_rel;
            if (newlog) {
                auto rel = std::filesystem::relative(*newlog, root);
                log_rel = rel.empty() ? newlog->filename().string() : rel.string();
            }
            std::ostringstream j;
            j << "{";
            j << "\"ok\":" << (rc == 0 ? "true" : "false") << ",";
            j << "\"exit_code\":" << rc << ",";
            j << "\"log\":\"" << json_escape(log_rel) << "\"";
            j << "}";
            send_json(cfd, 200, j.str());
            ::close(cfd);
            return;
        }

        send_json(cfd, 404, "{\"ok\":false,\"error\":\"not found\"}");
        ::close(cfd);
        }); // end per-connection thread lambda
        {
            std::lock_guard<std::mutex> lk(http_threads_mu);
            http_threads.push_back(std::move(t));
        }
    }

    // graceful shutdown — join all HTTP threads before destroying shared state
    {
        std::lock_guard<std::mutex> lk(http_threads_mu);
        for (auto& t : http_threads) {
            if (t.joinable()) t.join();
        }
        http_threads.clear();
    }
    ::close(sfd);
    if (workers > 0) {
        stop_workers.store(true);
        memq.shutdown();
        if (checkpoint_thread && checkpoint_thread->joinable()) checkpoint_thread->join();
        if (feeder_thread && feeder_thread->joinable()) feeder_thread->join();
        for (auto& t : worker_threads) if (t.joinable()) t.join();
        write_checkpoint();
    }
    return 0;
}

#else
int cmd_serve(int, char**) {
    std::cerr << "serve not supported on Windows build\n";
    return 2;
}
#endif
