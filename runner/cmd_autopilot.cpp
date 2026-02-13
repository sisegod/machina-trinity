#include "cmd_autopilot.h"
#include "cmd_run.h"
#include "runner_utils.h"

#include "machina/json_mini.h"

#include <atomic>
#include <csignal>
#include <iostream>
#include <thread>
#include <vector>

using namespace machina;

static std::atomic<bool> g_autopilot_running{true};

int cmd_autopilot(int argc, char** argv) {
    // Install signal handlers for graceful shutdown
    g_autopilot_running.store(true);
    std::signal(SIGTERM, [](int) { g_autopilot_running.store(false); });
    std::signal(SIGINT,  [](int) { g_autopilot_running.store(false); });

    std::cerr << "[WARN] 'autopilot' is a lightweight dev/debug mode.\n";
    std::cerr << "[WARN] For production use, prefer 'serve --workers N' which provides\n";
    std::cerr << "[WARN] WAL, crash recovery, checkpoints, HTTP API, and rate limiting.\n";

    const auto root = resolve_root(argv[0]);
    std::filesystem::path q = default_queue_dir(root);
    bool once = false;
    int sleepms = 500;
    int workers = 1;

    for (int i = 2; i < argc; i++) {
        std::string a = argv[i];
        if (a == "--once") { once = true; continue; }
        if (a == "--sleep_ms" && i + 1 < argc) { sleepms = std::atoi(argv[++i]); continue; }
        if (a == "--workers" && i + 1 < argc) { workers = std::atoi(argv[++i]); if (workers < 1) workers = 1; if (workers > 64) workers = 64; continue; }
        if (!a.empty() && a[0] != '-') { q = a; continue; }
    }

    if (!q.is_absolute()) q = root / q;
    ensure_queue_dirs(q);

    auto inbox = q / "inbox";
    auto processing = q / "processing";
    auto retry = q / "retry";

    std::cerr << "[autopilot] root=" << root << " queue=" << q << " workers=" << workers << "\n";

    auto worker_fn = [&](int wid) -> int {
        (void)wid;
        while (g_autopilot_running.load()) {
            if (wid == 0) {
                move_due_retries(retry, inbox);
            }

            auto jobs = list_inbox_json(inbox);
            if (jobs.empty()) {
                if (once) return 0;
                sleep_ms(sleepms);
                continue;
            }

            auto job = jobs.front();
            std::string base = job.filename().string();
            std::filesystem::path proc = processing / (base + ".processing");
            std::error_code ec;
            std::filesystem::rename(job, proc, ec);
            if (ec) {
                sleep_ms(10);
                continue;
            }

            // Delegate to shared job processing function
            JobResult jr = process_queue_job(proc, base, argv[0], root, q);
            (void)jr; // autopilot doesn't need WAL/counters
        }
        return 0;
    };

    if (workers <= 1) {
        return worker_fn(0);
    }

    std::vector<std::thread> th;
    th.reserve((size_t)workers);
    std::atomic<int> first_rc{-999};
    for (int i = 0; i < workers; i++) {
        th.emplace_back([&, i]() {
            int rc = worker_fn(i);
            int exp = -999;
            (void)first_rc.compare_exchange_strong(exp, rc);
        });
    }
    for (auto& t : th) t.join();
    int rc = first_rc.load();
    return (rc == -999) ? 0 : rc;
}
