#include <iostream>
#include <fstream>
#include <sstream>
#include <string>
#include <cstdio>
#include <dirent.h>
#include <sys/statvfs.h>

std::string exec_cmd(const char* cmd) {
    char buf[256];
    std::string result;
    FILE* pipe = popen(cmd, "r");
    if (!pipe) return "ERROR";
    while (fgets(buf, sizeof(buf), pipe)) result += buf;
    pclose(pipe);
    return result;
}

void cpu_info() {
    std::ifstream f("/proc/loadavg");
    std::string line;
    if (std::getline(f, line)) {
        std::cout << "[CPU] Load Avg: " << line << std::endl;
    }
    std::ifstream cf("/proc/cpuinfo");
    int cores = 0;
    while (std::getline(cf, line)) {
        if (line.find("processor") == 0) cores++;
    }
    std::cout << "[CPU] Cores: " << cores << std::endl;
}

void mem_info() {
    std::ifstream f("/proc/meminfo");
    std::string line;
    long total=0, free_m=0, avail=0, buffers=0, cached=0;
    while (std::getline(f, line)) {
        std::istringstream iss(line);
        std::string key; long val;
        iss >> key >> val;
        if (key == "MemTotal:") total = val;
        else if (key == "MemFree:") free_m = val;
        else if (key == "MemAvailable:") avail = val;
        else if (key == "Buffers:") buffers = val;
        else if (key == "Cached:") cached = val;
    }
    long used = total - avail;
    double pct = (double)used / total * 100.0;
    std::cout << "[MEM] Total: " << total/1024 << " MB | Used: " << used/1024 << " MB | Free: " << avail/1024 << " MB (" << (int)pct << "%)" << std::endl;
}

void disk_info() {
    struct statvfs stat;
    if (statvfs("/", &stat) == 0) {
        unsigned long long total = (unsigned long long)stat.f_blocks * stat.f_frsize;
        unsigned long long free_d = (unsigned long long)stat.f_bfree * stat.f_frsize;
        unsigned long long used = total - free_d;
        double pct = (double)used / total * 100.0;
        std::cout << "[DISK] Total: " << total/1073741824 << " GB | Used: " << used/1073741824 << " GB | Free: " << free_d/1073741824 << " GB (" << (int)pct << "%)" << std::endl;
    }
}

void net_info() {
    std::ifstream f("/proc/net/dev");
    std::string line;
    int lineno = 0;
    while (std::getline(f, line)) {
        lineno++;
        if (lineno <= 2) continue;
        if (line.find("lo:") != std::string::npos) continue;
        std::istringstream iss(line);
        std::string iface;
        long rx_bytes, rx_packets;
        iss >> iface >> rx_bytes >> rx_packets;
        std::cout << "[NET] " << iface << " RX: " << rx_bytes/1048576 << " MB";
        // skip to tx
        long dummy, tx_bytes;
        for (int i=0; i<6; i++) iss >> dummy;
        iss >> tx_bytes;
        std::cout << " | TX: " << tx_bytes/1048576 << " MB" << std::endl;
    }
}

void uptime_info() {
    std::ifstream f("/proc/uptime");
    double up;
    f >> up;
    int days = (int)(up / 86400);
    int hours = (int)((up - days*86400) / 3600);
    int mins = (int)((up - days*86400 - hours*3600) / 60);
    std::cout << "[UPTIME] " << days << "d " << hours << "h " << mins << "m" << std::endl;
}

void top_procs() {
    std::string result = exec_cmd("ps aux --sort=-%mem | head -6");
    std::cout << "[TOP PROCS]" << std::endl << result;
}

int main() {
    std::cout << "====== MACHINA SYSTEM HEALTH ======" << std::endl;
    uptime_info();
    cpu_info();
    mem_info();
    disk_info();
    net_info();
    top_procs();
    std::cout << "===================================" << std::endl;
    return 0;
}