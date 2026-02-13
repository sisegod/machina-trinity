#pragma once
#include "state.h"
#include "types.h"

namespace machina {

// Transaction: DS -> DS_TMP -> commit/rollback
// Thread-safety: Tx is NOT thread-safe. The caller must hold an exclusive lock
// on the target DSState when calling commit(). Tx is non-copyable/non-movable
// to prevent accidental sharing across threads.
class Tx {
public:
    explicit Tx(const DSState& base);

    // Non-copyable, non-movable
    Tx(const Tx&) = delete;
    Tx& operator=(const Tx&) = delete;
    Tx(Tx&&) = delete;
    Tx& operator=(Tx&&) = delete;

    DSState& tmp();           // DS_TMP
    const DSState& base() const;

    // REQUIRES: caller must hold exclusive lock on target
    void commit(DSState& target);
    void rollback();

    // RFC6902-like patch describing changes from base -> tmp (computed on commit)
    const std::string& patch_json() const { return patch_json_; }

private:
    DSState base_;
    DSState tmp_;
    bool active_{true};
    std::string patch_json_{};
};

} // namespace machina
