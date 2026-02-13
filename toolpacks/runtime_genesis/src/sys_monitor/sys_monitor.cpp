#include <iostream>
#include <fstream>
#include <sstream>
#include <string>
#include <cstdio>
#include <cstring>
#include <dirent.h>
#include <sys/statvfs.h>
#include <unistd.h>

struct MemInfo {
    long total_kb;
    long avail_kb;
    long swap_total_kb;
    long swap_free_kb;
};

struct CpuLoad {
    double load1;
    double load5;
    double load15;
};

struct DiskInfo {
    double total_gb;
    double used_gb;
    double avail_gb;
    double use_pct;
};

MemInfo get_memory() {
    MemInfo m = {0,0,0,0};
    std::ifstream f("/proc/meminfo");
    std::string line;
    while(std::getline(f, line)) {
        if(line.find("MemTotal:") == 0) sscanf(line.c_str(), "MemTotal: %ld", &m.total_kb);
        if(line.find("MemAvailable:") == 0) sscanf(line.c_str(), "MemAvailable: %ld", &m.avail_kb);
        if(line.find("SwapTotal:") == 0) sscanf(line.c_str(), "SwapTotal: %ld", &m.swap_total_kb);
        if(line.find("SwapFree:") == 0) sscanf(line.c_str(), "SwapFree: %ld", &m.swap_free_kb);
    }
    return m;
}

CpuLoad get_cpu_load() {
    CpuLoad c = {0,0,0};
    std::ifstream f("/proc/loadavg");
    f >> c.load1 >> c.load5 >> c.load15;
    return c;
}

DiskInfo get_disk(const char* path) {
    DiskInfo d = {0,0,0,0};
    struct statvfs st;
    if(statvfs(path, &st) == 0) {
        double bs = (double)st.f_frsize;
        d.total_gb = (bs * st.f_blocks) / 1073741824.0;
        d.avail_gb = (bs * st.f_bavail) / 1073741824.0;
        d.used_gb = d.total_gb - (bs * st.f_bfree) / 1073741824.0;
        d.use_pct = (d.total_gb > 0) ? (d.used_gb / d.total_gb * 100.0) : 0;
    }
    return d;
}

int get_cpu_temp() {
    std::ifstream f("/sys/class/thermal/thermal_zone0/temp");
    int temp = 0;
    if(f.is_open()) { f >> temp; temp /= 1000; }
    return temp;
}

int count_processes() {
    int count = 0;
    DIR* dir = opendir("/proc");
    if(dir) {
        struct dirent* entry;
        while((entry = readdir(dir))) {
            bool is_pid = true;
            for(int i = 0; entry->d_name[i]; i++) {
                if(entry->d_name[i] < '0' || entry->d_name[i] > '9') { is_pid = false; break; }
            }
            if(is_pid) count++;
        }
        closedir(dir);
    }
    return count;
}

long get_uptime() {
    std::ifstream f("/proc/uptime");
    double up = 0;
    f >> up;
    return (long)up;
}

std::string get_top_mem_proc() {
    FILE* fp = popen("ps aux --sort=-%mem 2>/dev/null | head -6", "r");
    if(!fp) return "N/A";
    std::string result;
    char buf[512];
    while(fgets(buf, sizeof(buf), fp)) result += buf;
    pclose(fp);
    return result;
}

extern "C" {
    const char* tool_name() { return "sys_monitor"; }
    const char* tool_description() { return "System monitor: CPU, memory, disk, temp, processes"; }
    
    int tool_execute(const char* input, char* output, int max_len) {
        MemInfo mem = get_memory();
        CpuLoad cpu = get_cpu_load();
        DiskInfo disk = get_disk("/");
        int temp = get_cpu_temp();
        int procs = count_processes();
        long uptime = get_uptime();
        long days = uptime / 86400;
        long hours = (uptime % 86400) / 3600;
        long mins = (uptime % 3600) / 60;
        
        double mem_used_gb = (mem.total_kb - mem.avail_kb) / 1048576.0;
        double mem_total_gb = mem.total_kb / 1048576.0;
        double mem_pct = (mem.total_kb > 0) ? ((double)(mem.total_kb - mem.avail_kb) / mem.total_kb * 100.0) : 0;
        double swap_used_gb = (mem.swap_total_kb - mem.swap_free_kb) / 1048576.0;
        double swap_total_gb = mem.swap_total_kb / 1048576.0;
        
        std::string top_procs = get_top_mem_proc();
        
        snprintf(output, max_len,
            "=== SYSTEM MONITOR ==="
            "\n[Uptime] %ldd %ldh %ldm"
            "\n[CPU] Load: %.2f / %.2f / %.2f (1/5/15 min)"
            "\n[CPU Temp] %d C"
            "\n[Memory] %.1f / %.1f GB (%.1f%%)"
            "\n[Swap] %.1f / %.1f GB"
            "\n[Disk /] %.1f / %.1f GB (%.1f%% used, %.1f GB free)"
            "\n[Processes] %d running"
            "\n\n[Top Memory Processes]\n%s",
            days, hours, mins,
            cpu.load1, cpu.load5, cpu.load15,
            temp,
            mem_used_gb, mem_total_gb, mem_pct,
            swap_used_gb, swap_total_gb,
            disk.used_gb, disk.total_gb, disk.use_pct, disk.avail_gb,
            procs,
            top_procs.c_str()
        );
        return 0;
    }
}
