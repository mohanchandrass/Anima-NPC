#pragma once
#include <SKSE/SKSE.h>
#include "runtime_support.h"

namespace anima {
constexpr SKSE::PluginVersionData VersionMetadata()
{
    SKSE::PluginVersionData data;
    data.PluginVersion({0,5,3,0});
    data.PluginName("AnimaConnector"); data.AuthorName("Anima project");
    // Exact versions, not generic version independence. The post-629 bit is
    // unknown to the original 1.6.318 loader. Do not falsely claim NoStructUse.
    data.versionIndependence = 0;
    data.versionIndependenceEx = SKSE::PluginVersionData::kVersionIndependentEx_AddressLibraryV5;
    constexpr auto old = SupportedRuntimes[0];
    constexpr auto current = SupportedRuntimes[1];
    data.CompatibleVersions({REL::Version{old.major,old.minor,old.patch,old.build},
                             REL::Version{current.major,current.minor,current.patch,current.build}});
    return data;
}
}
