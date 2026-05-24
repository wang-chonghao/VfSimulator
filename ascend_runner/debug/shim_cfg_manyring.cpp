#include <dlfcn.h>
#include <fstream>
#include <sstream>
#include <string>

namespace camodel_file_config {
using Fn = std::string (*)(const std::string&);

std::string get_config_by_filename(const std::string& name) {
    if (name.find("manyring.csv") != std::string::npos) {
        std::ifstream in("/mnt/d/VfSimulator/ascend_runner/build/GeLU_optimized_pto_sim/manyring_override.txt");
        std::ostringstream ss;
        ss << in.rdbuf();
        return ss.str();
    }
    auto fn = reinterpret_cast<Fn>(dlsym(RTLD_NEXT, "_ZN19camodel_file_config22get_config_by_filenameERKSs"));
    if (!fn) {
        return std::string();
    }
    return fn(name);
}
}
