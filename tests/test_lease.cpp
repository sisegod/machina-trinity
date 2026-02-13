#include "test_common.h"
#include "machina/lease.h"

#include <thread>
#include <chrono>

int main() {
    using namespace machina;

    // Test 1: Tool tier classification
    {
        auto t0 = classify_tool_tier("AID.FILE.READ.v1", {"none"});
        expect_true(t0 == ToolTier::SAFE, "FILE.READ should be SAFE");

        auto t1 = classify_tool_tier("AID.FILE.WRITE.v1", {"write"});
        expect_true(t1 == ToolTier::WRITE, "FILE.WRITE should be WRITE");

        auto t2 = classify_tool_tier("AID.SHELL.EXEC.v1", {"exec"});
        expect_true(t2 == ToolTier::SYSTEM, "SHELL.EXEC should be SYSTEM");

        auto t3 = classify_tool_tier("AID.GENESIS.WRITE_FILE.v1", {});
        expect_true(t3 == ToolTier::DANGEROUS, "GENESIS should be DANGEROUS");

        auto tn = classify_tool_tier("AID.NET.HTTP_GET.v1", {});
        expect_true(tn == ToolTier::SYSTEM, "NET.HTTP_GET should be SYSTEM");
    }

    // Test 2: Issue and consume a lease
    {
        LeaseManager lm;
        auto tok = lm.issue_lease("AID.SHELL.EXEC.v1", 2, 10000, "test");
        expect_true(!tok.token_id.empty(), "token should have an id");
        expect_true(tok.tool_aid == "AID.SHELL.EXEC.v1", "token tool_aid");
        expect_true(tok.tier == 2, "token tier");
        expect_true(lm.active_count() == 1, "should have 1 active lease");

        std::string reason;
        bool ok = lm.verify_and_consume(tok.token_id, "AID.SHELL.EXEC.v1", &reason);
        expect_true(ok, "should verify successfully: " + reason);
        expect_true(lm.total_consumed() == 1, "consumed count");
    }

    // Test 3: Single-use — second consume should fail
    {
        LeaseManager lm;
        auto tok = lm.issue_lease("AID.SHELL.EXEC.v1", 2, 10000);
        lm.verify_and_consume(tok.token_id, "AID.SHELL.EXEC.v1");

        std::string reason;
        bool ok = lm.verify_and_consume(tok.token_id, "AID.SHELL.EXEC.v1", &reason);
        expect_true(!ok, "second consume should fail");
        expect_true(reason.find("consumed") != std::string::npos, "reason should mention consumed");
    }

    // Test 4: Tool mismatch
    {
        LeaseManager lm;
        auto tok = lm.issue_lease("AID.SHELL.EXEC.v1", 2, 10000);

        std::string reason;
        bool ok = lm.verify_and_consume(tok.token_id, "AID.FILE.WRITE.v1", &reason);
        expect_true(!ok, "wrong tool should fail");
        expect_true(reason.find("mismatch") != std::string::npos, "reason should mention mismatch");
    }

    // Test 5: Wildcard lease
    {
        LeaseManager lm;
        auto tok = lm.issue_lease("*", 2, 10000);

        bool ok = lm.verify_and_consume(tok.token_id, "AID.SHELL.EXEC.v1");
        expect_true(ok, "wildcard lease should match any tool");
    }

    // Test 6: Expired lease
    {
        LeaseManager lm;
        auto tok = lm.issue_lease("AID.SHELL.EXEC.v1", 2, 1000); // 1 second TTL (minimum)
        // Note: minimum clamp is 1s, so we sleep just over 1s
        std::this_thread::sleep_for(std::chrono::milliseconds(1100));

        std::string reason;
        bool ok = lm.verify_and_consume(tok.token_id, "AID.SHELL.EXEC.v1", &reason);
        expect_true(!ok, "expired lease should fail");
        expect_true(reason.find("expired") != std::string::npos, "reason should mention expired");
    }

    // Test 7: Not found
    {
        LeaseManager lm;
        std::string reason;
        bool ok = lm.verify_and_consume("nonexistent_token", "AID.SHELL.EXEC.v1", &reason);
        expect_true(!ok, "nonexistent token should fail");
        expect_true(reason.find("not found") != std::string::npos, "reason should mention not found");
    }

    // Test 8: requires_lease check
    {
        LeaseManager lm;
        expect_true(!lm.requires_lease("AID.FILE.READ.v1", {"none"}), "READ should not require lease");
        expect_true(lm.requires_lease("AID.SHELL.EXEC.v1", {"exec"}), "SHELL should require lease");
        expect_true(lm.requires_lease("AID.GENESIS.WRITE_FILE.v1", {}), "GENESIS should require lease");
    }

    // Test 9: GC
    {
        LeaseManager lm;
        lm.issue_lease("AID.SHELL.EXEC.v1", 2, 1000);
        expect_true(lm.active_count() == 1, "should have 1 active before gc");
        std::this_thread::sleep_for(std::chrono::milliseconds(1100));
        lm.gc();
        expect_true(lm.active_count() == 0, "gc should remove expired leases");
    }

    // Test 10: Stats
    {
        LeaseManager lm;
        auto t1 = lm.issue_lease("AID.SHELL.EXEC.v1", 2, 60000);
        auto t2 = lm.issue_lease("AID.FILE.WRITE.v1", 1, 60000);
        lm.verify_and_consume(t1.token_id, "AID.SHELL.EXEC.v1");
        lm.verify_and_consume("bad_token", "AID.FILE.WRITE.v1");

        expect_true(lm.total_issued() == 2, "total issued should be 2");
        expect_true(lm.total_consumed() == 1, "total consumed should be 1");
        expect_true(lm.total_rejected() == 1, "total rejected should be 1");
    }

    std::cerr << "test_lease: ALL PASSED" << std::endl;
    return 0;
}
