
#include <machina/plugin_api.h>
#include <cstdio>

extern "C" {
    const char* machina_plugin_name() { return "chain_test"; }
    const char* machina_plugin_version() { return "1.0.0"; }
    int machina_plugin_execute(const char* input, char* output, int output_size) {
        snprintf(output, output_size, "{\"ok\":true}");
        return 0;
    }
}
