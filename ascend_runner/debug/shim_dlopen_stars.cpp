#include <cstdio>
#include <cstdlib>
#include <dlfcn.h>

static void load_one(const char *tag, const char *path)
{
    if (path == nullptr || path[0] == '\0') {
        return;
    }
    dlerror();
    void *h = dlopen(path, RTLD_LAZY | RTLD_GLOBAL);
    if (h == nullptr) {
        std::fprintf(stderr, "[shim_dlopen_stars] %s load failed: %s\n", tag, dlerror());
    } else {
        std::fprintf(stderr, "[shim_dlopen_stars] %s loaded: %s\n", tag, path);
    }
}

__attribute__((constructor)) static void preload_stars_global()
{
    load_one("zlib", std::getenv("SIM_ZLIB_SO"));
    load_one("utility_pre", std::getenv("SIM_UTILITY_SO"));
    load_one("common", std::getenv("SIM_COMMON_SO"));
    load_one("utility_post", std::getenv("SIM_UTILITY_SO"));
    load_one("stars", std::getenv("SIM_STAR_SO"));
}