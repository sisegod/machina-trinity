#include "test_common.h"

#include "machina/cpq.h"

#include <string>
#include <thread>
#include <chrono>
#include <vector>

using machina::ConcurrentPriorityQueue;

int main() {
    ConcurrentPriorityQueue<std::string> q;

    q.push(5, "low");
    q.push(1, "hi1");
    q.push(1, "hi2");

    ConcurrentPriorityQueue<std::string>::Item it;
    expect_true(q.pop(it), "pop 1 should succeed");
    expect_true(it.priority == 1, "first item priority should be 1");
    std::string first = it.value;

    expect_true(q.pop(it), "pop 2 should succeed");
    expect_true(it.priority == 1, "second item priority should be 1");
    std::string second = it.value;

    // For equal priority, seq enforces FIFO
    expect_true(first == "hi1" && second == "hi2", "seq FIFO violated for same priority");

    expect_true(q.pop(it), "pop 3 should succeed");
    expect_true(it.priority == 5 && it.value == "low", "third item should be low priority");

    // Blocking pop should unblock on shutdown
    ConcurrentPriorityQueue<int> q2;
    bool popped = false;
    std::thread t([&] {
        ConcurrentPriorityQueue<int>::Item it2;
        bool ok = q2.pop(it2);
        popped = ok;
    });

    // Give the thread a moment to block
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    q2.shutdown();
    t.join();

    expect_true(popped == false, "pop should return false after shutdown on empty queue");

    return 0;
}
