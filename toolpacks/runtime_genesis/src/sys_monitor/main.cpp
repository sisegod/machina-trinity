#include <iostream>
#include <fstream>
#include <string>
#include <sstream>

std::string read_file(const std::string& path) {
    std::ifstream f(path);
    if (!f.is_open()) return "N/A";
    std::string content;
    std::getline(f, content);
    return content;
}

int main() {
    std::cout << "=== System Monitor ==" << std::endl;

    // Uptime
    std::ifstream up("/proc/uptime");
    if (up.is_open()) {
        double secs;
        up >> secs;
        int days = (int)(secs / 86400);
        int hours = (int)((secs - days*86400) / 3600);
        int mins = (int)((secs - days*86400 - hours*3600) / 60);
        std::cout << "Uptime: " + std::to_string(days) + "d " + std::to_string(hours) + "h " + std::to_string(mins) + "m" << std::endl;
    }

    // Memory
    std::ifstream mem("/proc/meminfo");
    if (mem.is_open()) {
        std::string line;
        long total=0, free_m=0, avail=0;
        while (std::getline(mem, line)) {
            std::istringstream iss(line);
            std::string key;
            long val;
            iss >> key >> val;
            if (key == "MemTotal:") total = val;
            else if (key == "MemFree:") free_m = val;
            else if (key == "MemAvailable:") avail = val;
        }
        long used = total - avail;
        std::cout << "Memory: " + std::to_string(used/1024) + "MB / " + std::to_string(total/1024) + "MB (" + std::to_string(used*100/total) + "%)" << std::endl;
    }

    // Load average
    std::string load = read_file("/proc/loadavg");
    std::cout << "Load: " + load << std::endl;

    // Hostname
    std::string host = read_file("/proc/sys/kernel/hostname");
    std::cout << "Host: " + host << std::endl;

    std::cout << "=== Done ==" << std::endl;
    return 0;
}