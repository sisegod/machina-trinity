#include "tools/tier0/proc_metrics.h"

#include "machina/tools.h"

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>

#ifdef __linux__
#include <unistd.h>
#endif

namespace machina {

static long long parse_first_integer(const std::string& s) {
    long long v = 0;
    bool saw = false;
    for (char c : s) {
        if (c >= '0' && c <= '9') {
            saw = true;
            v = v * 10 + (c - '0');
        } else if (saw) {
            break;
        }
    }
    return saw ? v : 0;
}

ToolResult tool_proc_self_metrics(const std::string& input_json, DSState& ds_tmp) {
    (void)input_json;
    (void)ds_tmp;
#ifdef __linux__
    const int pid = (int)getpid();

    long long rss_kb = 0;
    long long vmsize_kb = 0;
    long long threads = 0;

    {
        std::ifstream f("/proc/self/status");
        std::string line;
        while (std::getline(f, line)) {
            if (line.rfind("VmRSS:", 0) == 0) rss_kb = parse_first_integer(line);
            else if (line.rfind("VmSize:", 0) == 0) vmsize_kb = parse_first_integer(line);
            else if (line.rfind("Threads:", 0) == 0) threads = parse_first_integer(line);
        }
    }

    long long open_fds = 0;
    try {
        for (auto it = std::filesystem::directory_iterator("/proc/self/fd"); it != std::filesystem::directory_iterator(); ++it) {
            open_fds++;
        }
    } catch (...) {
        open_fds = -1;
    }

    std::ostringstream out;
    out << "{";
    out << "\"ok\":true,";
    out << "\"pid\":" << pid << ",";
    out << "\"rss_kb\":" << rss_kb << ",";
    out << "\"vmsize_kb\":" << vmsize_kb << ",";
    out << "\"threads\":" << threads << ",";
    out << "\"open_fds\":" << open_fds;
    out << "}";
    return {StepStatus::OK, out.str(), ""};
#else
    return {StepStatus::TOOL_ERROR, "{}", "proc_self_metrics is only supported on Linux"};
#endif
}

} // namespace machina
