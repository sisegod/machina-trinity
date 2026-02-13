#include "machina/tools.h"

#include "machina/embedding_provider.h"
#include "machina/json_mini.h"

#include <cstdlib>
#include <iomanip>
#include <sstream>

namespace machina {

static size_t getenv_size_t(const char* k, size_t defv) {
    if (const char* e = std::getenv(k)) {
        try { return (size_t)std::stoull(e); } catch (...) {}
    }
    return defv;
}

ToolResult tool_embed_text(const std::string& input_json, DSState& /*ds_tmp*/) {
    try {
        std::string text = json_mini::get_string(input_json, "text").value_or("");
        if (text.empty()) {
            return {StepStatus::TOOL_ERROR, "{}", "missing text"};
        }
        size_t dim = (size_t)json_mini::get_int(input_json, "dim")
            .value_or((int64_t)getenv_size_t("MACHINA_VECDB_DIM", 384));
        bool normalize = json_mini::get_bool(input_json, "normalize").value_or(true);
        if (dim == 0) dim = 384;
        if (dim > 1024) dim = 1024;

        auto er = embed_text_best_effort(text, dim);
        if (normalize) l2_normalize(er.embedding);

        std::ostringstream out;
        out << "{";
        out << "\"provider\":\"" << json_mini::json_escape(er.provider) << "\",";
        out << "\"dim\":" << dim << ",";
        if (!er.error.empty()) out << "\"warn\":\"" << json_mini::json_escape(er.error) << "\",";
        out << "\"embedding\":[";
        out.setf(std::ios::fixed);
        out << std::setprecision(6);
        for (size_t i = 0; i < er.embedding.size(); i++) {
            if (i) out << ",";
            out << er.embedding[i];
        }
        out << "]}";
        return {StepStatus::OK, out.str(), ""};
    } catch (const std::exception& e) {
        return {StepStatus::TOOL_ERROR, "{}", e.what()};
    }
}

} // namespace machina
