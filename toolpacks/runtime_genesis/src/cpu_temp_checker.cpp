// Genesis: CPU Temperature Checker Plugin
// Reads thermal zone and returns temperature
#include <fstream>
#include <sstream>
#include <string>

extern "C" {

const char* tool_name() { return "cpu_temp_checker"; }
const char* tool_description() { return "Check CPU temperature from thermal zones"; }

const char* tool_execute(const char* input_json) {
    static std::string result;
    std::ostringstream oss;

    // Read all thermal zones
    for (int i = 0; i < 10; i++) {
        std::string path = "/sys/class/thermal/thermal_zone" + std::to_string(i) + "/temp";
        std::ifstream f(path);
        if (!f.good()) break;

        int temp_millideg;
        f >> temp_millideg;
        double temp_c = temp_millideg / 1000.0;

        // Read zone type
        std::string type_path = "/sys/class/thermal/thermal_zone" + std::to_string(i) + "/type";
        std::ifstream tf(type_path);
        std::string zone_type = "unknown";
        if (tf.good()) std::getline(tf, zone_type);

        oss << "Zone " << i << " (" << zone_type << "): " << temp_c << " C\n";
    }

    if (oss.str().empty()) {
        result = "{\"error\": \"no thermal zones found\"}";
    } else {
        result = "{\"temperatures\": \"" + oss.str() + "\"}";
    }
    return result.c_str();
}

} // extern "C"
