#include <dlfcn.h>
#include <fstream>
#include <string>

namespace camodel_file_config {
using Fn = std::string (*)(const std::string&);

std::string get_config_by_filename(const std::string& name) {
    auto fn = reinterpret_cast<Fn>(dlsym(RTLD_NEXT, "_ZN19camodel_file_config22get_config_by_filenameERKSs"));
    if (!fn) {
        return std::string();
    }
    std::string out = fn(name);
    if (name.find("manyring.csv") != std::string::npos) {
        std::ofstream log("/mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/manyring_dump.txt", std::ios::binary);
        log << out;
    }
    return out;
}
}
