#pragma once

#include <string>

namespace machina {

// Lightweight persistent vector store under $MACHINA_ROOT/work/vectordb.
// Exposed via tools:
//  - AID.VECDB.UPSERT.v1
//  - AID.VECDB.QUERY.v1

// Helper for Memory subsystem: append a single text item into vectordb.
// Returns true on success; err (if provided) contains short reason.
bool vectordb_upsert_text(const std::string& stream, const std::string& text, const std::string& meta_json_raw, std::string* err);

} // namespace machina
