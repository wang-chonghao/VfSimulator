#include <cstdint>
#include <new>
#include <stdexcept>
#include <vector>

namespace shim_axi_ctor {
struct TAxiStreamBusConfigHack {
    char pad[0x40];
    std::vector<std::vector<unsigned int>> wrr_ratios;
};

struct TNodeWrrSchdMgrHack {
    unsigned int count;
    unsigned int pad0;
    TAxiStreamBusConfigHack* cfg;
    unsigned int node_idx;
    unsigned int pad1;
    std::vector<unsigned int> ratios;
    unsigned int total;
    unsigned int pad2;
    std::vector<unsigned int> prefix;
};
}

extern "C" void _ZN18AXI_STREAM_BUS_SIM15TNodeWrrSchdMgr5resetEv(shim_axi_ctor::TNodeWrrSchdMgrHack* self);

static void init_ctor(shim_axi_ctor::TNodeWrrSchdMgrHack* self, unsigned int node, shim_axi_ctor::TAxiStreamBusConfigHack* cfg) {
    self->count = 0;
    self->pad0 = 0;
    self->cfg = cfg;
    self->node_idx = node;
    self->pad1 = 0;
    new (&self->ratios) std::vector<unsigned int>();
    self->total = 0;
    self->pad2 = 0;
    new (&self->prefix) std::vector<unsigned int>();

    if (node >= cfg->wrr_ratios.size()) {
        throw std::out_of_range("shim_axi_ctor node idx");
    }

    self->ratios = cfg->wrr_ratios.at(node);
    self->count = static_cast<unsigned int>(self->ratios.size());
    self->prefix.resize(self->count, 0U);
    for (unsigned int i = 0; i < self->count; ++i) {
        self->total += self->ratios.at(i);
        self->prefix.at(i) = self->total;
    }

    _ZN18AXI_STREAM_BUS_SIM15TNodeWrrSchdMgr5resetEv(self);
}

extern "C" void _ZN18AXI_STREAM_BUS_SIM15TNodeWrrSchdMgrC2EjPNS_19TAxiStreamBusConfigE(
    shim_axi_ctor::TNodeWrrSchdMgrHack* self,
    unsigned int node,
    shim_axi_ctor::TAxiStreamBusConfigHack* cfg) {
    init_ctor(self, node, cfg);
}

extern "C" void _ZN18AXI_STREAM_BUS_SIM15TNodeWrrSchdMgrC1EjPNS_19TAxiStreamBusConfigE(
    shim_axi_ctor::TNodeWrrSchdMgrHack* self,
    unsigned int node,
    shim_axi_ctor::TAxiStreamBusConfigHack* cfg) {
    init_ctor(self, node, cfg);
}
