#pragma once

#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <queue>
#include <utility>
#include <vector>

namespace machina {

// ConcurrentPriorityQueue
// - Thread-safe push/pop
// - Blocking pop with shutdown()
// - Lower priority value => higher priority
//
// Intent: fast in-proc scheduling (daemon mode) while preserving file-queue safety as the source of truth.

template <typename T>
class ConcurrentPriorityQueue {
public:
    struct Item {
        int32_t priority{0};
        uint64_t seq{0};
        T value;
    };

private:
    struct Cmp {
        bool operator()(const Item& a, const Item& b) const {
            // std::priority_queue pops the "largest" element; invert so lower priority comes first.
            if (a.priority != b.priority) return a.priority > b.priority;
            // Earlier seq first.
            return a.seq > b.seq;
        }
    };

public:
    ConcurrentPriorityQueue() = default;

    void push(int32_t priority, T value) {
        std::lock_guard<std::mutex> lk(mu_);
        if (closed_) return;
        q_.push(Item{priority, seq_++, std::move(value)});
        cv_.notify_one();
    }

    // Returns false when shut down.
    bool pop(Item& out) {
        std::unique_lock<std::mutex> lk(mu_);
        cv_.wait(lk, [&]{ return closed_ || !q_.empty(); });
        if (q_.empty()) return false;
        out = q_.top();  // copy (const ref from priority_queue::top)
        q_.pop();
        return true;
    }

    void shutdown() {
        std::lock_guard<std::mutex> lk(mu_);
        closed_ = true;
        cv_.notify_all();
    }

    size_t size() const {
        std::lock_guard<std::mutex> lk(mu_);
        return q_.size();
    }

    bool closed() const {
        std::lock_guard<std::mutex> lk(mu_);
        return closed_;
    }

private:
    mutable std::mutex mu_;
    std::condition_variable cv_;
    std::priority_queue<Item, std::vector<Item>, Cmp> q_;
    uint64_t seq_{0};
    bool closed_{false};
};

} // namespace machina
