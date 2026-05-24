#include <dlfcn.h>
#include <fstream>
#include <iomanip>
#include <mutex>

namespace chi_interface {
class TSocCfg {
public:
    unsigned int catDiePortId(unsigned int x);
};
}

using Fn = unsigned int (*)(chi_interface::TSocCfg*, unsigned int);
static std::mutex g_mu;

namespace chi_interface {
unsigned int TSocCfg::catDiePortId(unsigned int x) {
    static Fn real_fn = reinterpret_cast<Fn>(dlsym(RTLD_NEXT, "_ZN13chi_interface7TSocCfg12catDiePortIdEj"));
    unsigned int y = real_fn ? real_fn(this, x) : x;
    {
        std::lock_guard<std::mutex> lock(g_mu);
        std::ofstream ofs("/tmp/src_fanout_catDiePortId.log", std::ios::app);
        ofs << "in=0x" << std::hex << x << " out=0x" << y << std::dec << "\n";
    }
    return y;
}
}
