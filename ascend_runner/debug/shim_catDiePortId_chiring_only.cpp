#include <dlfcn.h>
#include <string>

namespace chi_interface {
class TSocCfg {
public:
    unsigned int catDiePortId(unsigned int x);
};
}

using Fn = unsigned int (*)(chi_interface::TSocCfg*, unsigned int);

namespace chi_interface {
unsigned int TSocCfg::catDiePortId(unsigned int x) {
    static Fn real_fn = reinterpret_cast<Fn>(dlsym(RTLD_NEXT, "_ZN13chi_interface7TSocCfg12catDiePortIdEj"));
    Dl_info info{};
    void* ret = __builtin_return_address(0);
    if (dladdr(ret, &info) && info.dli_fname) {
        std::string so = info.dli_fname;
        if (so.find("libChiRingFabric.so") != std::string::npos) {
            return x;
        }
    }
    return real_fn ? real_fn(this, x) : x;
}
}