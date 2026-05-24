#include <cstdlib>
#include <cstdint>
#include <dlfcn.h>
#include <iostream>
#include <string>

#include "acl/acl.h"

namespace {

bool PreloadSimulatorStars()
{
    const char *simRoot = std::getenv("ASCEND_SIMULATOR_PATH");
    if (simRoot == nullptr || simRoot[0] == '\0') {
        return true;
    }

    const std::string libStars = std::string(simRoot) + "/camodel/libstars.so";
    void *handle = dlopen(libStars.c_str(), RTLD_NOW | RTLD_GLOBAL);
    if (handle == nullptr) {
        std::cerr << "Failed to preload simulator library: " << libStars << "\n";
        std::cerr << dlerror() << "\n";
        return false;
    }
    return true;
}

} // namespace

int main(int argc, char *argv[])
{
    (void)argc;
    (void)argv;

    if (!PreloadSimulatorStars()) {
        return 2;
    }

    std::cout << "Ascend ACL host runner scaffold.\n";
    std::cout << "TODO: implement aclInit/device setup/binary load/kernel launch.\n";
    return 0;
}