
#include <machina/plugin_api.h>
#include <cstring>
#include <cstdio>

extern "C" {
    const char* machina_plugin_name() { return "test_genesis_e2e"; }
    const char* machina_plugin_version() { return "1.0.0"; }
    int machina_plugin_execute(const char* input, char* output, int output_size) {
        snprintf(output, output_size, "{\"ok\":true,\"msg\":\"genesis works\"}");
        return 0;
    }
}
