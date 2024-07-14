CHANNEL_TYPE_CONFIGURATION = {
    "CT_1F02": {
        "inputDeviceFunctions": {
            "onOff": "FT_INDA.SOO",
            "brightness": "FT_INDA.ASC",
            "sceneControl": "FT_INDA.SC",
            "masterDimming": "FT_INDA.MD"
        },
        "outputDeviceFunctions": {
            "onOff": "FT_INDA.IOO",
            "brightness": "FT_INDA.ADV",
            "forcedState": "FT_INDA.IFOS"
        },
        "deviceParameters": {
            "applicationMode": "PT_INDA.APPLICATION_MODE_DUMMY"
        }
    },
    "CT_1F13": {
        "inputDeviceFunctions": {},
        "outputDeviceFunctions": {
            "sceneControl": "FT_INScS.SC",
            "masterDimming": "FT_INScS.MD"
        },
        "deviceParameters": {
            "applicationMode": "PT_INScS.APPLICATION_MODE_DUMMY"
        }
    },
    "CT_1F11": {
        "inputDeviceFunctions": {},
        "outputDeviceFunctions": {
            "buttonRocker": "FT_INGBRS.GBR"
        },
        "deviceParameters": {
            "applicationMode": "PT_INGBRS.APPLICATION_MODE_DUMMY"
        }
    },
    "CT_1F05": {
        "inputDeviceFunctions": {
            "onOff": "FT_INSArGO.SOO",
            "sceneControl": "FT_INSArGO.SC"
        },
        "outputDeviceFunctions": {
            "onOff": "FT_INSArGO.IOO",
            "forcedState": "FT_INSArGO.IFOS"
        },
        "deviceParameters": {
            "applicationMode": "PT_INSArGO.APPLICATION_MODE_DUMMY"
        }
    },
    "CT_1F03": {
        "inputDeviceFunctions": {
            "upDown": "FT_INBA.MUD",
            "stepUpDown": "FT_INBA.SSUD",
            "stop": "FT_INBA.STOP",
            "coverPosition": "FT_INBA.SAPBP",
            "tiltPosition": "FT_INBA.SAPSP",
            "sceneControl": "FT_INBA.SC"
        },
        "outputDeviceFunctions": {
            "upDown": "FT_INBA.IMUDS",
            "coverPosition": "FT_INBA.CAPBP",
            "tiltPosition": "FT_INBA.CAPSP",
            "forcedState": "FT_INBA.IFOS",
            "antiLockoutState": "FT_INBA.IALFS"
        },
        "deviceParameters": {
            "operationMode": "PT_INBA.OPMODE",
            "applicationMode": "PT_INBA.APPLICATION_MODE_DUMMY"
        }
    },
    "CT_1F01": {
        "inputDeviceFunctions": {
            "onOff": "FT_INSA.SOO",
            "sceneControl": "FT_INSA.SC",
        },
        "outputDeviceFunctions": {
            "onOff": "FT_INSA.IOO",
            "forcedState": "FT_INSA.IFOS",
        },
        "deviceParameters": {
            "applicationMode": "PT_INSA.APPLICATION_MODE_DUMMY"
        }
    },
    "CT_1F01_DUMMY": {
        "inputDeviceFunctions": {
            "onOff": "FT_INSA.SOO",
            "sceneControl": "FT_INSA.SC",
        },
        "outputDeviceFunctions": {
            "onOff": "FT_INSA.IOO",
            "forcedState": "FT_INSA.IFOS",
        },
        "deviceParameters": {
            "applicationMode": "PT_INSA.APPLICATION_MODE_DUMMY"
        }
    },
    "CT_1F1B": {
        "inputDeviceFunctions": {},
        "outputDeviceFunctions": {
            "triggerStart": "FT_INMOVS.TSPT",
            "brightness": "FT_INMOVS.BA",
        },
        "deviceParameters": {
            "applicationMode": "PT_INMOVS.APPLICATION_MODE_DUMMY"
        }
    },
    "CT_1F08": {
        "inputDeviceFunctions": {
            "onOff": "FT_INSAv2.SOO",
            "sceneControl": "FT_INSAv2.SC"
        },
        "outputDeviceFunctions": {
            "onOff": "FT_INSAv2.IOO",
            "forcedState": "FT_INSAv2.IFOS",
        },
        "deviceParameters": {}
    },
    "CT_1F12": {
        "inputDeviceFunctions": {},
        "outputDeviceFunctions": {
            "buttonRocker": "FT_INLS.GBR",
            "onOff": "FT_INLS.SOO"
        },
        "deviceParameters": {
            "applicationMode": "PT_INLS.APPLICATION_MODE_DUMMY"
        }
    },
    "CT_1F1E": {
        "inputDeviceFunctions": {},
        "outputDeviceFunctions": {
            "buttonRocker": "FT_INBS.GBR",
            "upDown": "FT_INBS.MUD",
            "stop": "FT_INBS.STOP"
        },
        "deviceParameters": {
            "applicationMode": "PT_INBS.APPLICATION_MODE_DUMMY"
        }
    },
    "CT_1F0B": {
        "inputDeviceFunctions": {
            "onOff": "FT_INLAv2_SA.SOO",
            "sceneControl": "FT_INLAv2_SA.SC"
        },
        "outputDeviceFunctions": {
            "onOff": "FT_INLAv2_SA.IOO",
            "forcedState": "FT_INLAv2_SA.IFOS",
            "blockedState": "FT_INLAv2_SA.IBFS"
        },
        "deviceParameters": {
            "applicationMode": "PT_INLAv2_SA.APPLICATION_MODE_DUMMY",
        }
    },
    "CT_1F0C": {
        "inputDeviceFunctions": {
            "onOff": "FT_INLAv2_DA.SOO",
            "brightness": "FT_INLAv2_DA.ASC",
            "sceneControl": "FT_INLAv2_DA.SC",
            "masterDimming": "FT_INLAv2_DA.MD"
        },
        "outputDeviceFunctions": {
            "onOff": "FT_INLAv2_DA.IOO",
            "brightness": "FT_INLAv2_DA.ADV",
            "forcedState": "FT_INLAv2_DA.IFOS",
            "blockedState": "FT_INLAv2_DA.IBFS"
        },
        "deviceParameters": {
            "applicationMode": "PT_INLAv2_DA.APPLICATION_MODE_DUMMY"
        }
    },
    "CT_1F0E": {
        "inputDeviceFunctions": {
            "onOff": "FT_INLNS3.SOO",
            "brightness": "FT_INLNS3.ASC",
            "sceneControl": "FT_INLNS3.SC"
        },
        "outputDeviceFunctions": {
            "blockedState": "FT_INLNS3.IBFS"
        },
        "deviceParameters": {
            "applicationMode": "PT_INLNS3.APPLICATION_MODE_DUMMY"
        }
    },
    "CT_1F10": {
        "inputDeviceFunctions": {
            "upDown": "FT_INBAv3.MUD",
            "stepUpDown": "FT_INBAv3.SSUD",
            "stop": "FT_INBAv3.STOP",
            "coverPosition": "FT_INBAv3.SAPBP",
            "tiltPosition": "FT_INBAv3.SAPSP",
            "coverTiltPosition": "FT_INBAv3.SAPBSP",
            "sceneControl": "FT_INBAv3.SC"
        },
        "outputDeviceFunctions": {
            "upDown": "FT_INBAv3.IMUDS",
            "coverPosition": "FT_INBAv3.CAPBP",
            "tiltPosition": "FT_INBAv3.CAPSP",
            "forcedState": "FT_INBAv3.IFOS",
            "antiLockoutState": "FT_INBAv3.IALFS",
            "blockedState": "FT_INBAv3.IBFS"
        },
        "deviceParameters": {
            "operationMode": "PT_INBAv3.OPMODE",
            "applicationMode": "PT_INBAv3.APPLICATION_MODE_DUMMY"
        }
    },
    "CT_1F0D": {
        "inputDeviceFunctions": {
            "onOff": "FT_INLAv2_SAS.SOO",
            "sceneControl": "FT_INLAv2_SAS.SC"
        },
        "outputDeviceFunctions": {
            "onOff": "FT_INLAv2_SAS.IOO",
            "forcedState": "FT_INLAv2_SAS.IFOS",
            "blockedState": "FT_INLAv2_SAS.IBFS"
        },
        "deviceParameters": {
            "applicationMode": "PT_INLAv2_SAS.APPLICATION_MODE_DUMMY"
        }
    },
    "CT_1F0E_SA": {
        "inputDeviceFunctions": {
            "onOff": "FT_INLNS3_SA.SOO",
            "sceneControl": "FT_INLNS3_SA.SC"
        },
        "outputDeviceFunctions": {
            "blockedState": "FT_INLNS3_SA.IBFS"
        },
        "deviceParameters": {
            "applicationMode": "PT_INLNS3_SA.APPLICATION_MODE_DUMMY"
        }
    },
    "CT_1F19": {
        "inputDeviceFunctions": {},
        "outputDeviceFunctions": {
            "voltage": "FT_INES.VOLT",
            "current": "FT_INES.CURR",
            "powerActive": "FT_INES.ACPW",
            "powerReactive": "FT_INES.REPW",
            "powerApparent": "FT_INES.APPW",
            "energyActive": "FT_INES.ABAE"
        },
        "deviceParameters": {
            "applicationMode": "PT_INES.APPLICATION_MODE_DUMMY"
        }
    },
    "CT_1F18": {
        "inputDeviceFunctions": {},
        "outputDeviceFunctions": {
            "sceneControl": "FT_INThScS.SC",
            "brightness": "FT_INThScS.CAVTH1",
        },
        "deviceParameters": {
            "applicationMode": "PT_INThScS.APPLICATION_MODE_DUMMY"
        }
    },
    "CT_1F09": {
        "inputDeviceFunctions": {
            "onOff": "FT_INSAv3.SOO",
            "applicationModeSwitch": "FT_INSAv3.AMS",
            "sceneControl": "FT_INSAv3.SC",
        },
        "outputDeviceFunctions": {
            "onOff": "FT_INSAv3.IOO",
            "forcedState": "FT_INSAv3.IFOS",
        },
        "deviceParameters": {}
    }
}
