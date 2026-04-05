/*
 * crc_utils.c - Optimized CRC Engine with Multi-Arch Dispatch & Benchmark
 * 
 * Compiles with: gcc -O3 -fPIC -shared crc_utils.c -o crc_utils.so
 * Architecture flags injected automatically by __init__.py build system.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include "crc_utils.h"
#include <string.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <sched.h>
#include <time.h>
#include <errno.h>

/* ──────────────────────────────────────────────────────────────────────────
 *  §1  STATIC BASE TABLES (Cache-Line Aligned)
 * ────────────────────────────────────────────────────────────────────────── */
static const uint8_t crc8_t0[256] __aligned(CRC_CACHE_LINE_SIZE) = {
    0x00,0x31,0x62,0x53,0xC4,0xF5,0xA6,0x97,0xB9,0x88,0xDB,0xEA,0x7D,0x4C,0x1F,0x2E,
    0x43,0x72,0x21,0x10,0x87,0xB6,0xE5,0xD4,0xFA,0xCB,0x98,0xA9,0x3E,0x0F,0x5C,0x6D,
    0x86,0xB7,0xE4,0xD5,0x42,0x73,0x20,0x11,0x3F,0x0E,0x5D,0x6C,0xFB,0xCA,0x99,0xA8,
    0xC5,0xF4,0xA7,0x96,0x01,0x30,0x63,0x52,0x7C,0x4D,0x1E,0x2F,0xB8,0x89,0xDA,0xEB,
    0x3D,0x0C,0x5F,0x6E,0xF9,0xC8,0x9B,0xAA,0x84,0xB5,0xE6,0xD7,0x40,0x71,0x22,0x13,
    0x7E,0x4F,0x1C,0x2D,0xBA,0x8B,0xD8,0xE9,0xC7,0xF6,0xA5,0x94,0x03,0x32,0x61,0x50,
    0xBB,0x8A,0xD9,0xE8,0x7F,0x4E,0x1D,0x2C,0x02,0x33,0x60,0x51,0xC6,0xF7,0xA4,0x95,
    0xF8,0xC9,0x9A,0xAB,0x3C,0x0D,0x5E,0x6F,0x41,0x70,0x23,0x12,0x85,0xB4,0xE7,0xD6,
    0x7A,0x4B,0x18,0x29,0xBE,0x8F,0xDC,0xED,0xC3,0xF2,0xA1,0x90,0x07,0x36,0x65,0x54,
    0x39,0x08,0x5B,0x6A,0xFD,0xCC,0x9F,0xAE,0x80,0xB1,0xE2,0xD3,0x44,0x75,0x26,0x17,
    0xF1,0xC0,0x93,0xA2,0x35,0x04,0x57,0x66,0x48,0x79,0x2A,0x1B,0x8C,0xBD,0xEE,0xDF,
    0x12,0x23,0x70,0x41,0xD6,0xE7,0xB4,0x85,0xAB,0x9A,0xC9,0xF8,0x6F,0x5E,0x0D,0x3C,
    0xD2,0xE3,0xB0,0x81,0x16,0x27,0x74,0x45,0x6B,0x5A,0x09,0x38,0xAF,0x9E,0xCD,0xFC,
    0xA1,0x90,0xC3,0xF2,0x65,0x54,0x07,0x36,0x18,0x29,0x7A,0x4B,0xDC,0xED,0xBE,0x8F,
    0x64,0x55,0x06,0x37,0xA0,0x91,0xC2,0xF3,0xDD,0xEC,0xBF,0x8E,0x19,0x28,0x7B,0x4A,
    0x26,0x17,0x44,0x75,0xE2,0xD3,0x80,0xB1,0x9F,0xAE,0xFD,0xCC,0x5B,0x6A,0x39,0x08
};

static const uint16_t crc16_t0[256] __aligned(CRC_CACHE_LINE_SIZE) = {
    0x0000,0x1021,0x2042,0x3063,0x4084,0x50A5,0x60C6,0x70E7,0x8108,0x9129,0xA14A,0xB16B,0xC18C,0xD1AD,0xE1CE,0xF1EF,
    0x1231,0x0210,0x3273,0x2252,0x52B5,0x4294,0x72F7,0x62D6,0x9339,0x8318,0xB37B,0xA35A,0xD3BD,0xC39C,0xF3FF,0xE3DE,
    0x2462,0x3443,0x0420,0x1401,0x64E6,0x74C7,0x44A4,0x5485,0xA56A,0xB54B,0x8528,0x9509,0xE5EE,0xF5CF,0xC5AC,0xD58D,
    0x3653,0x2672,0x1611,0x0630,0x76D7,0x66F6,0x5695,0x46B4,0xB75B,0xA77A,0x9719,0x8738,0xF7DF,0xE7FE,0xD79D,0xC7BC,
    0x48C4,0x58E5,0x6886,0x78A7,0x0840,0x1861,0x2802,0x3823,0xC9CC,0xD9ED,0xE98E,0xF9AF,0x8948,0x9969,0xA90A,0xB92B,
    0x5AF5,0x4AD4,0x7AB7,0x6A96,0x1A71,0x0A50,0x3A33,0x2A12,0xDBFD,0xCBDC,0xFBBF,0xEB9E,0x9B79,0x8B58,0xBB3B,0xAB1A,
    0x6CA6,0x7C87,0x4CE4,0x5CC5,0x2C22,0x3C03,0x0C60,0x1C41,0xEDAE,0xFD8F,0xCDEC,0xDDCD,0xAD2A,0xBD0B,0x8D68,0x9D49,
    0x7E97,0x6EB6,0x5ED5,0x4EF4,0x3E13,0x2E32,0x1E51,0x0E70,0xFF9F,0xEFBE,0xDFDD,0xCFFC,0xBF1B,0xAF3A,0x9F59,0x8F78,
    0x9188,0x81A9,0xB1CA,0xA1EB,0xD10C,0xC12D,0xF14E,0xE16F,0x1080,0x00A1,0x30C2,0x20E3,0x5004,0x4025,0x7046,0x6067,
    0x83B9,0x9398,0xA3FB,0xB3DA,0xC33D,0xD31C,0xE37F,0xF35E,0x02B1,0x1290,0x22F3,0x32D2,0x4235,0x5214,0x6277,0x7256,
    0xB5EA,0xA5CB,0x95A8,0x8589,0xF56E,0xE54F,0xD52C,0xC50D,0x34E2,0x24C3,0x14A0,0x0481,0x7466,0x6447,0x5424,0x4405,
    0xA7DB,0xB7FA,0x8799,0x97B8,0xE75F,0xF77E,0xC71D,0xD73C,0x26D3,0x36F2,0x0691,0x16B0,0x6657,0x7676,0x4615,0x5634,
    0xD94C,0xC96D,0xF90E,0xE92F,0x99C8,0x89E9,0xB98A,0xA9AB,0x5844,0x4865,0x7806,0x6827,0x18C0,0x08E1,0x3882,0x28A3,
    0xCB7D,0xDB5C,0xEB3F,0xFB1E,0x8BF9,0x9BD8,0xABBB,0xBB9A,0x4A75,0x5A54,0x6A37,0x7A16,0x0AF1,0x1AD0,0x2AB3,0x3A92,
    0xFD2E,0xED0F,0xDD6C,0xCD4D,0xBDAA,0xAD8B,0x9DE8,0x8DC9,0x7C26,0x6C07,0x5C64,0x4C45,0x3CA2,0x2C83,0x1CE0,0x0CC1,
    0xEF1F,0xFF3E,0xCF5D,0xDF7C,0xAF9B,0xBFBA,0x8FD9,0x9FF8,0x6E17,0x7E36,0x4E55,0x5E74,0x2E93,0x3EB2,0x0ED1,0x1EF0
};

static const uint32_t crc32_t0[256] __aligned(CRC_CACHE_LINE_SIZE) = {
    0x00000000U,0x77073096U,0xEE0E612CU,0x990951BAU,0x076DC419U,0x706AF48FU,0xE963A535U,0x9E6495A3U,
    0x0EDB8832U,0x79DCB8A4U,0xE0D5E91EU,0x97D2D988U,0x09B64C2BU,0x7EB17CBDU,0xE7B82D07U,0x90BF1D91U,
    0x1DB71064U,0x6AB020F2U,0xF3B97148U,0x84BE41DEU,0x1ADAD47DU,0x6DDDE4EBU,0xF4D4B551U,0x83D385C7U,
    0x136C9856U,0x646BA8C0U,0xFD62F97AU,0x8A65C9ECU,0x14015C4FU,0x63066CD9U,0xFA0F3D63U,0x8D080DF5U,
    0x3B6E20C8U,0x4C69105EU,0xD56041E4U,0xA2677172U,0x3C03E4D1U,0x4B04D447U,0xD20D85FDU,0xA50AB56BU,
    0x35B5A8FAU,0x42B2986CU,0xDBBBC9D6U,0xACBCF940U,0x32D86CE3U,0x45DF5C75U,0xDCD60DCFU,0xABD13D59U,
    0x26D930ACU,0x51DE003AU,0xC8D75180U,0xBFD06116U,0x21B4F4B5U,0x56B3C423U,0xCFBA9599U,0xB8BDA50FU,
    0x2802B89EU,0x5F058808U,0xC60CD9B2U,0xB10BE924U,0x2F6F7C87U,0x58684C11U,0xC1611DABU,0xB6662D3DU,
    0x76DC4190U,0x01DB7106U,0x98D220BCU,0xEFD5102AU,0x71B18589U,0x06B6B51FU,0x9FBFE4A5U,0xE8B8D433U,
    0x7807C9A2U,0x0F00F934U,0x9609A88EU,0xE10E9818U,0x7F6A0DBBU,0x086D3D2DU,0x91646C97U,0xE6635C01U,
    0x6B6B51F4U,0x1C6C6162U,0x856530D8U,0xF262004EU,0x6C0695EDU,0x1B01A57BU,0x8208F4C1U,0xF50FC457U,
    0x65B0D9C6U,0x12B7E950U,0x8BBEB8EAU,0xFCB9887CU,0x62DD1DDFU,0x15DA2D49U,0x8CD37CF3U,0xFBD44C65U,
    0x4DB26158U,0x3AB551CEU,0xA3BC0074U,0xD4BB30E2U,0x4ADFA541U,0x3DD895D7U,0xA4D1C46DU,0xD3D6F4FBU,
    0x4369E96AU,0x346ED9FCU,0xAD678846U,0xDA60B8D0U,0x44042D73U,0x33031DE5U,0xAA0A4C5FU,0xDD0D7CC9U,
    0x5005713CU,0x270241AAU,0xBE0B1010U,0xC90C2086U,0x5768B525U,0x206F85B3U,0xB966D409U,0xCE61E49FU,
    0x5EDEF90EU,0x29D9C998U,0xB0D09822U,0xC7D7A8B4U,0x59B33D17U,0x2EB40D81U,0xB7BD5C3BU,0xC0BA6CADU,
    0xEDB88320U,0x9ABFB3B6U,0x03B6E20CU,0x74B1D29AU,0xEAD54739U,0x9DD277AFU,0x04DB2615U,0x73DC1683U,
    0xE3630B12U,0x94643B84U,0x0D6D6A3EU,0x7A6A5AA8U,0xE40ECF0BU,0x9309FF9DU,0x0A00AE27U,0x7D079EB1U,
    0xF00F9344U,0x8708A3D2U,0x1E01F268U,0x6906C2FEU,0xF762575DU,0x806567CBU,0x196C3671U,0x6E6B06E7U,
    0xFED41B76U,0x89D32BE0U,0x10DA7A5AU,0x67DD4ACCU,0xF9B9DF6FU,0x8EBEEFF9U,0x17B7BE43U,0x60B08ED5U,
    0xD6D6A3E8U,0xA1D1937EU,0x38D8C2C4U,0x4FDFF252U,0xD1BB67F1U,0xA6BC5767U,0x3FB506DDU,0x48B2364BU,
    0xD80D2BDAU,0xAF0A1B4CU,0x36034AF6U,0x41047A60U,0xDF60EFC3U,0xA867DF55U,0x316E8EEFU,0x4669BE79U,
    0xCB61B38CU,0xBC66831AU,0x256FD2A0U,0x5268E236U,0xCC0C7795U,0xBB0B4703U,0x220216B9U,0x5505262FU,
    0xC5BA3BBEU,0xB2BD0B28U,0x2BB45A92U,0x5CB36A04U,0xC2D7FFA7U,0xB5D0CF31U,0x2CD99E8BU,0x5BDEAE1DU,
    0x9B64C2B0U,0xEC63F226U,0x756AA39CU,0x026D930AU,0x9C0906A9U,0xEB0E363FU,0x72076785U,0x05005713U,
    0x95BF4A82U,0xE2B87A14U,0x7BB12BAEU,0x0CB61B38U,0x92D28E9BU,0xE5D5BE0DU,0x7CDCEFB7U,0x0BDBDF21U,
    0x86D3D2D4U,0xF1D4E242U,0x68DDB3F8U,0x1FDA836EU,0x81BE16CDU,0xF6B9265BU,0x6FB077E1U,0x18B74777U,
    0x88085AE6U,0xFF0F6A70U,0x66063BCAU,0x11010B5CU,0x8F659EFFU,0xF862AE69U,0x616BFFD3U,0x166CCF45U,
    0xA00AE278U,0xD70DD2EEU,0x4E048354U,0x3903B3C2U,0xA7672661U,0xD06016F7U,0x4969474DU,0x3E6E77DBU,
    0xAED16A4AU,0xD9D65ADCU,0x40DF0B66U,0x37D83BF0U,0xA9BCAE53U,0xDEBB9EC5U,0x47B2CF7FU,0x30B5FFE9U,
    0xBDBDF21CU,0xCABAC28AU,0x53B39330U,0x24B4A3A6U,0xBAD03605U,0xCDD70693U,0x54DE5729U,0x23D967BFU,
    0xB3667A2EU,0xC4614AB8U,0x5D681B02U,0x2A6F2B94U,0xB40BBE37U,0xC30C8EA1U,0x5A05DF1BU,0x2D02EF8DU
};

static const uint32_t crc32c_t0[256] __aligned(CRC_CACHE_LINE_SIZE) = {
    0x00000000U,0xF26B8303U,0xE13B70F7U,0x1350F3F4U,0xC79A971FU,0x35F1141CU,0x26A1E7E8U,0xD4CA64EBU,
    0x8AD958CFU,0x78B2DBCCU,0x6BE22838U,0x9989AB3BU,0x4D43CFD0U,0xBF284CD3U,0xAC78BF27U,0x5E133C24U,
    0x105EC76FU,0xE235446CU,0xF165B798U,0x030E349BU,0xD7C45070U,0x25AFD373U,0x36FF2087U,0xC494A384U,
    0x9A879FA0U,0x68EC1CA3U,0x7BBCEF57U,0x89D76C54U,0x5D1D08BFU,0xAF768BBCU,0xBC267848U,0x4E4DFB4BU,
    0x20BD8EDEU,0xD2D60DDDU,0xC186FE29U,0x33ED7D2AU,0xE72719C1U,0x154C9AC2U,0x061C6936U,0xF477EA35U,
    0xAA64D611U,0x580F5512U,0x4B5FA6E6U,0xB93425E5U,0x6DFE410EU,0x9F95C20DU,0x8CC531F9U,0x7EAEB2FAU,
    0x30E349B1U,0xC288CAB2U,0xD1D83946U,0x23B3BA45U,0xF779DEAEU,0x05125DADU,0x1642AE59U,0xE4292D5AU,
    0xBA3A117EU,0x4851927DU,0x5B016189U,0xA96AE28AU,0x7DA08661U,0x8FCB0562U,0x9C9BF696U,0x6EF07595U,
    0x417B1DBCU,0xB3109EBFU,0xA0406D4BU,0x522BEE48U,0x86E18AA3U,0x748A09A0U,0x67DAFA54U,0x95B17957U,
    0xCBA24573U,0x39C9C670U,0x2A993584U,0xD8F2B687U,0x0C38D26CU,0xFE53516FU,0xED03A29BU,0x1F682198U,
    0x5125DAD3U,0xA34E59D0U,0xB01EAA24U,0x42752927U,0x96BF4DCCU,0x64D4CECFU,0x77843D3BU,0x85EFBE38U,
    0xDBFC821CU,0x2997011FU,0x3AC7F2EBU,0xC8AC71E8U,0x1C661503U,0xEE0D9600U,0xFD5D65F4U,0x0F36E6F7U,
    0x61C69362U,0x93AD1061U,0x80FDE395U,0x72966096U,0xA65C047DU,0x5437877EU,0x4767748AU,0xB50CF789U,
    0xEB1FCBADU,0x197448AEU,0x0A24BB5AU,0xF84F3859U,0x2C855CB2U,0xDEEEDFB1U,0xCDBE2C45U,0x3FD5AF46U,
    0x7198540DU,0x83F3D70EU,0x90A324FAU,0x62C8A7F9U,0xB602C312U,0x44694011U,0x5739B3E5U,0xA55230E6U,
    0xFB410CC2U,0x092A8FC1U,0x1A7A7C35U,0xE811FF36U,0x3CDB9BDDU,0xCEB018DEU,0xDDE0EB2AU,0x2F8B6829U,
    0x82F63B78U,0x709DB87BU,0x63CD4B8FU,0x91A6C88CU,0x456CAC67U,0xB7072F64U,0xA457DC90U,0x563C5F93U,
    0x082F63B7U,0xFA44E0B4U,0xE9141340U,0x1B7F9043U,0xCFB5F4A8U,0x3DDE77ABU,0x2E8E845FU,0xDCE5075CU,
    0x92A8FC17U,0x60C37F14U,0x73938CE0U,0x81F80FE3U,0x55326B08U,0xA759E80BU,0xB4091BFFU,0x466298FCU,
    0x1871A4D8U,0xEA1A27DBU,0xF94AD42FU,0x0B21572CU,0xDFEB33C7U,0x2D80B0C4U,0x3ED04330U,0xCCBBC033U,
    0xA24BB5A6U,0x502036A5U,0x4370C551U,0xB11B4652U,0x65D122B9U,0x97BAA1BAU,0x84EA524EU,0x7681D14DU,
    0x2892ED69U,0xDAF96E6AU,0xC9A99D9EU,0x3BC21E9DU,0xEF087A76U,0x1D63F975U,0x0E330A81U,0xFC588982U,
    0xB21572C9U,0x407EF1CAU,0x532E023EU,0xA145813DU,0x758FE5D6U,0x87E466D5U,0x94B49521U,0x66DF1622U,
    0x38CC2A06U,0xCAA7A905U,0xD9F75AF1U,0x2B9CD9F2U,0xFF56BD19U,0x0D3D3E1AU,0x1E6DCDEEU,0xEC064EEDU,
    0xC38D26C4U,0x31E6A5C7U,0x22B65633U,0xD0DDD530U,0x0417B1DBU,0xF67C32D8U,0xE52CC12CU,0x1747422FU,
    0x49547E0BU,0xBB3FFD08U,0xA86F0EFCU,0x5A048DFFU,0x8ECEE914U,0x7CA56A17U,0x6FF599E3U,0x9D9E1AE0U,
    0xD3D3E1ABU,0x21B862A8U,0x32E8915CU,0xC083125FU,0x144976B4U,0xE622F5B7U,0xF5720643U,0x07198540U,
    0x590AB964U,0xAB613A67U,0xB831C993U,0x4A5A4A90U,0x9E902E7BU,0x6CFBAD78U,0x7FAB5E8CU,0x8DC0DD8FU,
    0xE330A81AU,0x115B2B19U,0x020BD8EDU,0xF0605BEEU,0x24AA3F05U,0xD6C1BC06U,0xC5914FF2U,0x37FACCF1U,
    0x69E9F0D5U,0x9B8273D6U,0x88D28022U,0x7AB90321U,0xAE7367CAU,0x5C18E4C9U,0x4F48173DU,0xBD23943EU,
    0xF36E6F75U,0x0105EC76U,0x12551F82U,0xE03E9C81U,0x34F4F86AU,0xC69F7B69U,0xD5CF889DU,0x27A40B9EU,
    0x79B737BAU,0x8BDCB4B9U,0x988C474DU,0x6AE7C44EU,0xBE2DA0A5U,0x4C4623A6U,0x5F16D052U,0xAD7D5351U
};

/* ──────────────────────────────────────────────────────────────────────────
 *  §2  SLICE-BY-N SOFTWARE CORES (Always Available)
 * ────────────────────────────────────────────────────────────────────────── */
static uint8_t  crc8_s[3][256] __aligned(CRC_CACHE_LINE_SIZE);
static uint16_t crc16_shift4_hi[256], crc16_shift4_lo[256], crc16_d3[256], crc16_d2[256], crc16_d1[256] __aligned(CRC_CACHE_LINE_SIZE);
static uint32_t crc32_s[7][256] __aligned(CRC_CACHE_LINE_SIZE);
static uint32_t crc32c_s[7][256] __aligned(CRC_CACHE_LINE_SIZE);



static __always_inline void build_crc8_slices(void) {
    for (int b = 0; b < 256; b++) crc8_s[0][b] = crc8_t0[crc8_t0[b]];
    for (int b = 0; b < 256; b++) crc8_s[1][b] = crc8_t0[crc8_s[0][b]];
    for (int b = 0; b < 256; b++) crc8_s[2][b] = crc8_t0[crc8_s[1][b]];
}

static __always_inline void build_crc16_slices(void) {
    #define STEP16(c) ((uint16_t)(((c)<<8) ^ crc16_t0[((c)>>8)&0xFF]))
    for (int b = 0; b < 256; b++) {
        uint16_t c = (uint16_t)(b << 8);
        c = STEP16(c); c = STEP16(c); c = STEP16(c); c = STEP16(c); crc16_shift4_hi[b] = c;
        c = (uint16_t)b;
        c = STEP16(c); c = STEP16(c); c = STEP16(c); c = STEP16(c); crc16_shift4_lo[b] = c;
        c = crc16_t0[b];
        c = STEP16(c); c = STEP16(c); c = STEP16(c); crc16_d3[b] = c;
        c = crc16_t0[b];
        c = STEP16(c); c = STEP16(c); crc16_d2[b] = c;
        c = crc16_t0[b];
        c = STEP16(c); crc16_d1[b] = c;
    }
    #undef STEP16
}

static __always_inline void build_crc32_slices(const uint32_t *t0, uint32_t s[7][256]) {
    for (int b = 0; b < 256; b++) s[0][b] = t0[t0[b] & 0xFF] ^ (t0[b] >> 8);
    for (int sl = 1; sl < 7; sl++)
        for (int b = 0; b < 256; b++)
            s[sl][b] = t0[s[sl-1][b] & 0xFF] ^ (s[sl-1][b] >> 8);
}

static __hot uint8_t crc8_sw(const uint8_t *data, size_t len, uint8_t init) {
    uint8_t crc = init;
    while (len >= 4) {
        crc = crc8_s[2][crc ^ data[0]] ^ crc8_s[1][data[1]] ^ crc8_s[0][data[2]] ^ crc8_t0[data[3]];
        data += 4; len -= 4;
    }
    while (len--) crc = crc8_t0[crc ^ *data++];
    return crc;
}

static __hot uint16_t crc16_sw(const uint8_t *data, size_t len, uint16_t init) {
    uint16_t crc = init;
    while (len >= 4) {
        crc = crc16_shift4_hi[crc >> 8] ^ crc16_shift4_lo[crc & 0xFF] ^
              crc16_d3[data[0]] ^ crc16_d2[data[1]] ^ crc16_d1[data[2]] ^ crc16_t0[data[3]];
        data += 4; len -= 4;
    }
    while (len--) crc = (uint16_t)((crc << 8) ^ crc16_t0[((crc >> 8) ^ *data++) & 0xFF]);
    return crc;
}

static __hot uint32_t crc32_slice8_core(const uint8_t *data, size_t len, uint32_t init,
                                        const uint32_t *t0, uint32_t slices[7][256]) {
    uint32_t crc = init ^ 0xFFFFFFFFU;
    while (len && ((uintptr_t)data & 3)) { crc = t0[(crc ^ *data++) & 0xFF] ^ (crc >> 8); len--; }
    while (len >= 8) {
        uint32_t lo, hi;
        memcpy(&lo, data, 4); memcpy(&hi, data + 4, 4);
        lo ^= crc;
        crc = slices[6][lo & 0xFF] ^ slices[5][(lo >> 8) & 0xFF] ^
              slices[4][(lo >> 16) & 0xFF] ^ slices[3][lo >> 24] ^
              slices[2][hi & 0xFF] ^ slices[1][(hi >> 8) & 0xFF] ^
              slices[0][(hi >> 16) & 0xFF] ^ t0[hi >> 24];
        data += 8; len -= 8;
    }
    while (len--) crc = t0[(crc ^ *data++) & 0xFF] ^ (crc >> 8);
    return crc ^ 0xFFFFFFFFU;
}

static __hot uint32_t crc32_sw(const uint8_t *d, size_t n, uint32_t i) {
    return crc32_slice8_core(d, n, i, crc32_t0, crc32_s);
}
static __hot uint32_t crc32c_sw(const uint8_t *d, size_t n, uint32_t i) {
    return crc32_slice8_core(d, n, i, crc32c_t0, crc32c_s);
}

/* ──────────────────────────────────────────────────────────────────────────
 *  §3  x86-64 ACCELERATED PATHS
 * ────────────────────────────────────────────────────────────────────────── */
#if defined(__x86_64__) || defined(__i386__)
#include <cpuid.h>
#include <immintrin.h>

#if defined(__PCLMUL__) || defined(__SSE4_2__)
__attribute__((target("sse4.2,pclmul")))
static __hot uint16_t crc16_pclmul(const uint8_t *data, size_t len, uint16_t init) {
    if (len < 32) return crc16_sw(data, len, init);
    __m128i k = _mm_set_epi32(0, 0x8000, 0, 0x0002);
    __m128i acc = _mm_loadu_si128((const __m128i *)data);
    uint16_t init_be = (init >> 8) | (init << 8);
    acc = _mm_xor_si128(acc, _mm_slli_si128(_mm_cvtsi32_si128(init_be), 14));
    data += 16; len -= 16;
    while (len >= 16) {
        __m128i blk = _mm_loadu_si128((const __m128i *)data);
        __m128i lo = _mm_clmulepi64_si128(acc, k, 0x00);
        __m128i hi = _mm_clmulepi64_si128(acc, k, 0x11);
        acc = _mm_xor_si128(_mm_xor_si128(lo, hi), blk);
        data += 16; len -= 16;
    }
    uint32_t lo32 = (uint32_t)_mm_extract_epi32(acc, 0);
    uint64_t q = ((uint64_t)lo32 * 0x10811U) >> 32;
    uint16_t crc = (uint16_t)(lo32 ^ (uint32_t)(q * 0x1021U));
    return ((crc >> 8) | (crc << 8));
}

__attribute__((target("sse4.2,pclmul")))
static __hot uint32_t crc32_pclmul(const uint8_t *data, size_t len, uint32_t init) {
    if (len < 64) return crc32_sw(data, len, init);
    __m128i k = _mm_set_epi32(0, 0x1C6E4156U, 0, 0x154442BDU);
    __m128i acc = _mm_loadu_si128((const __m128i *)data);
    acc = _mm_xor_si128(acc, _mm_cvtsi32_si128((int)(init ^ 0xFFFFFFFFU)));
    data += 16; len -= 16;
    while (len >= 16) {
        __m128i blk = _mm_loadu_si128((const __m128i *)data);
        __m128i lo = _mm_clmulepi64_si128(acc, k, 0x00);
        __m128i hi = _mm_clmulepi64_si128(acc, k, 0x11);
        acc = _mm_xor_si128(_mm_xor_si128(lo, hi), blk);
        data += 16; len -= 16;
    }
    __m128i mu = _mm_set_epi32(0, 1, 0, (int)0xDEBB20E3U);
    __m128i poly = _mm_set_epi32(0, 1, 0, (int)0xEDB88320U);
    __m128i t0v = _mm_clmulepi64_si128(acc, mu, 0x10);
    __m128i t1v = _mm_clmulepi64_si128(t0v, poly, 0x00);
    uint32_t crc = (uint32_t)_mm_extract_epi32(_mm_xor_si128(acc, t1v), 1) ^ 0xFFFFFFFFU;
    return len ? crc32_slice8_core(data, len, crc, crc32_t0, crc32_s) : crc;
}

__attribute__((target("sse4.2,pclmul")))
static __hot uint32_t crc32c_pclmul(const uint8_t *data, size_t len, uint32_t init) {
    if (len < 64) return crc32c_sw(data, len, init);
    __m128i k = _mm_set_epi32(0, 0x493C7D27U, 0, 0x0DD45AABU);
    __m128i acc = _mm_loadu_si128((const __m128i *)data);
    acc = _mm_xor_si128(acc, _mm_cvtsi32_si128((int)(init ^ 0xFFFFFFFFU)));
    data += 16; len -= 16;
    while (len >= 16) {
        __m128i blk = _mm_loadu_si128((const __m128i *)data);
        __m128i lo = _mm_clmulepi64_si128(acc, k, 0x00);
        __m128i hi = _mm_clmulepi64_si128(acc, k, 0x11);
        acc = _mm_xor_si128(_mm_xor_si128(lo, hi), blk);
        data += 16; len -= 16;
    }
    __m128i mu = _mm_set_epi32(0, 1, 0, (int)0xDEA713F1U);
    __m128i poly = _mm_set_epi32(0, 1, 0, (int)0x82F63B78U);
    __m128i t0v = _mm_clmulepi64_si128(acc, mu, 0x10);
    __m128i t1v = _mm_clmulepi64_si128(t0v, poly, 0x00);
    uint32_t crc = (uint32_t)_mm_extract_epi32(_mm_xor_si128(acc, t1v), 1) ^ 0xFFFFFFFFU;
    return len ? crc32_slice8_core(data, len, crc, crc32c_t0, crc32c_s) : crc;
}
#endif

#if defined(__AVX512F__) && defined(__VPCLMULQDQ__)
__attribute__((target("avx512f,avx512dq,avx512bw,avx512vl,vpclmulqdq")))
static __hot uint32_t crc32c_vpclmul(const uint8_t *data, size_t len, uint32_t init) {
    if (len < 512) return crc32c_pclmul(data, len, init);
    __m512i k = _mm512_set_epi32(0,(int)0xDDC0152BU,0,(int)0x1C291D04U,0,(int)0xDDC0152BU,0,(int)0x1C291D04U,0,(int)0xDDC0152BU,0,(int)0x1C291D04U,0,(int)0xDDC0152BU,0,(int)0x1C291D04U);
    __m512i a0 = _mm512_loadu_si512((const __m512i *)(data + 0));
    __m512i a1 = _mm512_loadu_si512((const __m512i *)(data + 64));
    __m512i a2 = _mm512_loadu_si512((const __m512i *)(data + 128));
    __m512i a3 = _mm512_loadu_si512((const __m512i *)(data + 192));
    a0 = _mm512_xor_si512(a0, _mm512_zextsi128_si512(_mm_cvtsi32_si128((int)(init ^ 0xFFFFFFFFU))));
    data += 256; len -= 256;
    while (len >= 256) {
        a0 = _mm512_ternarylogic_epi64(_mm512_clmulepi64_epi128(a0,k,0x00), _mm512_clmulepi64_epi128(a0,k,0x11), _mm512_loadu_si512((const __m512i*)data), 0x96);
        a1 = _mm512_ternarylogic_epi64(_mm512_clmulepi64_epi128(a1,k,0x00), _mm512_clmulepi64_epi128(a1,k,0x11), _mm512_loadu_si512((const __m512i*)(data+64)), 0x96);
        a2 = _mm512_ternarylogic_epi64(_mm512_clmulepi64_epi128(a2,k,0x00), _mm512_clmulepi64_epi128(a2,k,0x11), _mm512_loadu_si512((const __m512i*)(data+128)), 0x96);
        a3 = _mm512_ternarylogic_epi64(_mm512_clmulepi64_epi128(a3,k,0x00), _mm512_clmulepi64_epi128(a3,k,0x11), _mm512_loadu_si512((const __m512i*)(data+192)), 0x96);
        data += 256; len -= 256;
    }
    __m256i b0 = _mm256_xor_si256(_mm512_castsi512_si256(a0), _mm512_extracti64x4_epi64(a0,1));
    __m256i b1 = _mm256_xor_si256(_mm512_castsi512_si256(a1), _mm512_extracti64x4_epi64(a1,1));
    __m256i b2 = _mm256_xor_si256(_mm512_castsi512_si256(a2), _mm512_extracti64x4_epi64(a2,1));
    __m256i b3 = _mm256_xor_si256(_mm512_castsi512_si256(a3), _mm512_extracti64x4_epi64(a3,1));
    __m128i acc = _mm_xor_si128(_mm256_castsi256_si128(_mm256_xor_si256(_mm256_xor_si256(b0,b1),_mm256_xor_si256(b2,b3))), _mm256_extracti128_si256(_mm256_xor_si256(_mm256_xor_si256(b0,b1),_mm256_xor_si256(b2,b3)),1));
    __m128i mu = _mm_set_epi32(0,1,0,(int)0xDEA713F1U);
    __m128i poly = _mm_set_epi32(0,1,0,(int)0x82F63B78U);
    uint32_t crc = (uint32_t)_mm_extract_epi32(_mm_xor_si128(acc, _mm_clmulepi64_si128(_mm_clmulepi64_si128(acc, mu, 0x10), poly, 0x00)), 1) ^ 0xFFFFFFFFU;
    return len ? crc32c_sw(data, len, crc) : crc;
}
#endif

static int x86_has_pclmul(void) { unsigned a,b,c,d; return __get_cpuid(1,&a,&b,&c,&d) && ((c>>1)&1); }
static int x86_has_avx512(void) { unsigned a,b,c,d,eax,edx; if(!__get_cpuid_count(7,0,&a,&b,&c,&d)) return 0; asm volatile("xgetbv":"=a"(eax),"=d"(edx):"c"(0)); return ((eax&0xE6)==0xE6) && ((c>>10)&1) && ((d>>11)&1); }
#endif

/* ──────────────────────────────────────────────────────────────────────────
 *  §4  ARM64/32 ACCELERATED PATHS
 * ────────────────────────────────────────────────────────────────────────── */
#if defined(__aarch64__) || defined(__arm__)
#include <arm_acle.h>
#if defined(__ARM_FEATURE_CRC32)
__attribute__((target("+crc")))
static __hot uint32_t crc32_arm3way(const uint8_t *data, size_t len, uint32_t init) {
    uint32_t crc = init ^ 0xFFFFFFFFU;
    size_t chunk = 1024;
    while (len >= 3*chunk) {
        uint32_t a=crc, b=0, c=0;
        for (size_t i=0; i<chunk; i+=8) {
            uint64_t w0,w1,w2;
            memcpy(&w0,data+i,8); memcpy(&w1,data+i+chunk,8); memcpy(&w2,data+i+2*chunk,8);
            a = __crc32d(a, w0); b = __crc32d(b, w1); c = __crc32d(c, w2);
        }
        crc = a^b^c; data += 3*chunk; len -= 3*chunk;
    }
    while (len>=8) { uint64_t w; memcpy(&w,data,8); crc = __crc32d(crc,w); data+=8; len-=8; }
    while (len--) crc = __crc32b(crc,*data++);
    return crc ^ 0xFFFFFFFFU;
}
__attribute__((target("+crc")))
static __hot uint32_t crc32c_arm3way(const uint8_t *data, size_t len, uint32_t init) {
    uint32_t crc = init ^ 0xFFFFFFFFU;
    size_t chunk = 1024;
    while (len >= 3*chunk) {
        uint32_t a=crc, b=0, c=0;
        for (size_t i=0; i<chunk; i+=8) {
            uint64_t w0,w1,w2;
            memcpy(&w0,data+i,8); memcpy(&w1,data+i+chunk,8); memcpy(&w2,data+i+2*chunk,8);
            a = __crc32cd(a, w0); b = __crc32cd(b, w1); c = __crc32cd(c, w2);
        }
        crc = a^b^c; data += 3*chunk; len -= 3*chunk;
    }
    while (len>=8) { uint64_t w; memcpy(&w,data,8); crc = __crc32cd(crc,w); data+=8; len-=8; }
    while (len--) crc = __crc32cb(crc,*data++);
    return crc ^ 0xFFFFFFFFU;
}
#endif
#if defined(__aarch64__) && defined(__ARM_FEATURE_CRYPTO)
#include <arm_neon.h>
__attribute__((target("+crc+crypto")))
static __hot uint32_t crc32_arm_pmull(const uint8_t *data, size_t len, uint32_t init) {
    if (len < 64) return crc32_sw(data, len, init);
    uint64x2_t k = vcombine_u64(vcreate_u64(0x154442BDULL), vcreate_u64(0x1C6E4156ULL));
    uint8x16_t acc = vld1q_u8(data);
    uint32x4_t v = vreinterpretq_u32_u8(acc);
    v = vsetq_lane_u32(vgetq_lane_u32(v,0)^(init^0xFFFFFFFFU), v, 0);
    acc = vreinterpretq_u8_u32(v);
    data += 16; len -= 16;
    while (len >= 16) {
        uint8x16_t blk = vld1q_u8(data);
        poly128_t lo = vmull_p64((poly64_t)vgetq_lane_u64(vreinterpretq_u64_u8(acc),0), (poly64_t)vgetq_lane_u64(k,0));
        poly128_t hi = vmull_high_p64(vreinterpretq_p64_u8(acc), vreinterpretq_p64_u64(k));
        acc = veorq_u8(veorq_u8(vreinterpretq_u8_p128(lo), vreinterpretq_u8_p128(hi)), blk);
        data += 16; len -= 16;
    }
    uint64x2_t bar = vcombine_u64(vcreate_u64(0xDEBB20E3ULL), vcreate_u64(0xEDB88320ULL));
    poly128_t t0 = vmull_p64((poly64_t)vgetq_lane_u64(vreinterpretq_u64_u8(acc),0), (poly64_t)vgetq_lane_u64(bar,0));
    poly128_t t1 = vmull_p64((poly64_t)vgetq_lane_u64(vreinterpretq_u64_p128(t0),0), (poly64_t)vgetq_lane_u64(bar,1));
    uint32_t crc = vgetq_lane_u32(veorq_u32(vreinterpretq_u32_u8(acc), vreinterpretq_u32_p128(t1)), 1) ^ 0xFFFFFFFFU;
    return len ? crc32_sw(data, len, crc) : crc;
}
#endif
#endif

/* ──────────────────────────────────────────────────────────────────────────
 *  §5  RUNTIME DISPATCH & WARMUP
 * ────────────────────────────────────────────────────────────────────────── */
typedef uint8_t  (*fn_crc8_t) (const uint8_t*, size_t, uint8_t);
typedef uint16_t (*fn_crc16_t)(const uint8_t*, size_t, uint16_t);
typedef uint32_t (*fn_crc32_t)(const uint8_t*, size_t, uint32_t);

static struct __aligned(CRC_CACHE_LINE_SIZE) {
    fn_crc8_t  f8;
    fn_crc16_t f16;
    fn_crc32_t f32;
    fn_crc32_t f32c;
} g_dispatch;

static atomic_int g_override = -1, g_warmed = 0;

static void init_dispatch(void) {
    crc_arch_t arch = (atomic_load(&g_override) >= 0) ? (crc_arch_t)atomic_load(&g_override) : crc_utils_detect_arch();
    g_dispatch.f8 = crc8_sw;
    g_dispatch.f16 = crc16_sw;
    g_dispatch.f32 = crc32_sw;
    g_dispatch.f32c = crc32c_sw;

    switch (arch) {
#if defined(__x86_64__) || defined(__i386__)
#if defined(__PCLMUL__) || defined(__SSE4_2__)
        case CRC_ARCH_X86_VPCLMUL: g_dispatch.f16 = crc16_pclmul; g_dispatch.f32 = crc32_sw; g_dispatch.f32c = crc32c_vpclmul; break;
        case CRC_ARCH_X86_PCLMUL:  g_dispatch.f16 = crc16_pclmul; g_dispatch.f32 = crc32_pclmul; g_dispatch.f32c = crc32c_pclmul; break;
#endif
#endif
#if defined(__aarch64__) && defined(__ARM_FEATURE_CRC32) && defined(__ARM_FEATURE_CRYPTO)
        case CRC_ARCH_ARMV8_PMULL_EOR3:
        case CRC_ARCH_ARMV8_PMULL: g_dispatch.f16 = crc16_sw; g_dispatch.f32 = crc32_arm_pmull; g_dispatch.f32c = crc32c_arm3way; break;
#endif
#if defined(__aarch64__) || defined(__arm__)
#if defined(__ARM_FEATURE_CRC32)
        case CRC_ARCH_ARMV8_CRC: g_dispatch.f16 = crc16_sw; g_dispatch.f32 = crc32_arm3way; g_dispatch.f32c = crc32c_arm3way; break;
#endif
#endif
        default: break;
    }
    barrier();
    atomic_store(&g_warmed, 1);
}

__visible void crc_utils_warmup(void) {
    if (unlikely(atomic_load(&g_warmed))) return;
    build_crc8_slices(); build_crc16_slices();
    build_crc32_slices(crc32_t0, crc32_s);
    build_crc32_slices(crc32c_t0, crc32c_s);
    init_dispatch();
}

/* ──────────────────────────────────────────────────────────────────────────
 *  §6  PUBLIC API
 * ────────────────────────────────────────────────────────────────────────── */
__visible uint8_t  crc8_compute(const uint8_t *d, size_t n, uint8_t i)  { if (unlikely(!atomic_load(&g_warmed))) crc_utils_warmup(); return g_dispatch.f8(d, n, i); }
__visible uint16_t crc16_ccitt_compute(const uint8_t *d, size_t n, uint16_t i) { if (unlikely(!atomic_load(&g_warmed))) crc_utils_warmup(); return g_dispatch.f16(d, n, i); }
__visible uint32_t crc32_compute(const uint8_t *d, size_t n, uint32_t i)   { if (unlikely(!atomic_load(&g_warmed))) crc_utils_warmup(); return g_dispatch.f32(d, n, i); }
__visible uint32_t crc32c_compute(const uint8_t *d, size_t n, uint32_t i)  { if (unlikely(!atomic_load(&g_warmed))) crc_utils_warmup(); return g_dispatch.f32c(d, n, i); }

/* ──────────────────────────────────────────────────────────────────────────
 *  §7  RT UTILITIES
 * ────────────────────────────────────────────────────────────────────────── */
__visible int crc_utils_lock_memory(void) { return mlockall(MCL_CURRENT | MCL_FUTURE) == 0 ? 0 : -errno; }
__visible int crc_utils_set_rt_scheduler(int p) { if (p<1||p>99) { errno=EINVAL; return -1; } struct sched_param s={.sched_priority=p}; return sched_setscheduler(0, SCHED_FIFO, &s); }
__visible int crc_utils_pin_to_cpu(int c) { cpu_set_t set; CPU_ZERO(&set); CPU_SET(c, &set); return sched_setaffinity(0, sizeof(set), &set); }
__visible void *crc_utils_alloc_aligned(size_t sz, size_t al) { if (al<CRC_CACHE_LINE_SIZE) al=CRC_CACHE_LINE_SIZE; sz = (sz+al-1)&~(al-1); void *p; if (posix_memalign(&p, al, sz)) return NULL; memset(p, 0, sz); return p; }
__visible void crc_utils_prefetch_data(const void *ptr, size_t len) { if (!ptr||!len) return; const char *p=ptr, *end=p+len; for (; p+64<=end; p+=64) prefetch_read(p); }

/* ──────────────────────────────────────────────────────────────────────────
 *  §8  DIAGNOSTICS & BENCHMARK
 * ────────────────────────────────────────────────────────────────────────── */
__visible crc_arch_t crc_utils_detect_arch(void) {
    int ov = atomic_load(&g_override); if (ov>=0) return (crc_arch_t)ov;
#if defined(__x86_64__) || defined(__i386__)
    if (x86_has_avx512()) return CRC_ARCH_X86_VPCLMUL;
    if (x86_has_pclmul()) return CRC_ARCH_X86_PCLMUL;
    return CRC_ARCH_GENERIC;
#elif defined(__aarch64__)
    return CRC_ARCH_ARMV8_PMULL_EOR3;
#elif defined(__arm__) && defined(__ARM_FEATURE_CRC32)
    return CRC_ARCH_ARMV8_CRC;
#elif defined(__riscv) && defined(__riscv_zbc)
    return CRC_ARCH_RISCV_ZBC;
#else
    return CRC_ARCH_GENERIC;
#endif
}
__visible void crc_utils_set_override_arch(crc_arch_t a) { atomic_store(&g_override, (int)a); atomic_store(&g_warmed, 0); }
__visible const char *crc_utils_arch_name(crc_arch_t a) {
    const char *n[] = {"generic/slice-by-N","x86 SSE4.2","x86 PCLMUL","x86 VPCLMUL","ARM CRC32","ARM PMULL","ARM PMULL+EOR3","RISC-V Zbc"};
    return (a>=0 && a<8) ? n[a] : "unknown";
}

__visible crc_bench_result_t crc_utils_run_benchmark(size_t buf_sz, uint64_t iters) {
    crc_utils_warmup();
    uint8_t *buf = crc_utils_alloc_aligned(buf_sz, CRC_CACHE_LINE_SIZE);
    if (!buf) return (crc_bench_result_t){0};
    memset(buf, 0xAA, buf_sz);

    struct timespec t0, t1;
    double mb_s(double elapsed_ns, double bytes) { return (bytes / 1e6) / (elapsed_ns / 1e9); }
    crc_bench_result_t res = {.buf_size=buf_sz, .iterations=iters, .arch_tier=crc_utils_detect_arch()};

    #define BENCH(fn_name, init_val, res_field) \
        clock_gettime(CLOCK_MONOTONIC, &t0); \
        for (uint64_t i=0; i<iters; i++) fn_name(buf, buf_sz, init_val); \
        clock_gettime(CLOCK_MONOTONIC, &t1); \
        res.res_field = mb_s((t1.tv_sec-t0.tv_sec)*1e9 + (t1.tv_nsec-t0.tv_nsec), (double)(buf_sz*iters));

    BENCH(crc8_compute, 0, crc8_mbs);
    BENCH(crc16_ccitt_compute, 0x1D0F, crc16_mbs);
    BENCH(crc32_compute, 0, crc32_mbs);
    BENCH(crc32c_compute, 0, crc32c_mbs);
    #undef BENCH

    free(buf);
    return res;
}