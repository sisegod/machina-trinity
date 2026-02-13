#pragma once
#include <string>

namespace machina {

enum class Profile { DEV, PROD };

// Detect profile from MACHINA_PROFILE env var. Default: DEV.
Profile detect_profile();

// Returns string name of profile.
const char* profile_name(Profile p);

// Apply profile defaults: sets env vars that are not already set.
// DEV: lenient (no fsync, genesis enabled, no seccomp, generous timeouts)
// PROD: strict (fsync on, genesis disabled, seccomp enabled, tight timeouts)
void apply_profile_defaults(Profile p);

} // namespace machina
