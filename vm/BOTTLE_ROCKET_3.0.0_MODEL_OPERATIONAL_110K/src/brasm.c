#define _POSIX_C_SOURCE 200809L
#include "brasm.h"
#include "brvm.h"

#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define ALINES 512u
#define LLEN 256u
#define LABELS 256u

typedef struct { char name[32]; uint32_t ip; } alabel;

static const char *const ops[BR_OPCODES] = {
    "NOP","MOVI","MOV","JMP","JZ","JNZ","HALT","ADD","SUB","MUL",
    "DIVU","MODU","AND","OR","XOR","NOT","SHL","SHR","CMP","LOAD",
    "STORE","PUSH","POP","SVC"
};
static const char *const modes[4] = {"WRAP","CHECKED","SATURATE","TRAPPING"};
static const char *const caps[8] = {
    "CONTROL","ARITH","MEMORY","STACK","SERVICE","STATE","UPDATE","DIAG"
};
static const char *const services[9] = {
    "YIELD","STATUS","REVOKE","DELEGATE","CONFIGURE","COMMIT","DIAG_EVENT",
    "SHA256","ED25519_VERIFY"
};

static char *trim(char *s) {
    char *e;
    while (isspace((unsigned char)*s)) ++s;
    e = s + strlen(s);
    while (e != s && isspace((unsigned char)e[-1])) *--e = 0;
    return s;
}

static int named(const char *s, const char *const *table, unsigned n) {
    unsigned i;
    for (i = 0; i < n; ++i) if (strcmp(s, table[i]) == 0) return (int)i;
    return -1;
}

static int number(const char *s, uint64_t *v) {
    char *e;
    unsigned long long x;
    if (s == NULL || *s == '-' || *s == '+') return -1;
    errno = 0;
    x = strtoull(s, &e, 0);
    if (*s == 0 || *e != 0 || errno == ERANGE) return -1;
    *v = (uint64_t)x;
    return 0;
}

static int regno(const char *s, char kind) {
    uint64_t v;
    if (s == NULL || s[0] != kind || number(s + 1, &v) != 0 || v >= 16) return -1;
    return (int)v;
}

static int capmask(char *s, uint32_t *mask) {
    char *p, *save = NULL;
    int n;
    *mask = 0;
    for (p = strtok_r(s, "|", &save); p != NULL; p = strtok_r(NULL, "|", &save)) {
        n = named(p, caps, 8);
        if (n < 0 || (*mask & (1u << (unsigned)n)) != 0) return -1;
        *mask |= 1u << (unsigned)n;
    }
    return *mask == 0 ? -1 : 0;
}

static int label_value(const char *s, const alabel *labels, unsigned count,
                       uint64_t *value) {
    unsigned i;
    if (number(s, value) == 0) return 0;
    for (i = 0; i < count; ++i) if (strcmp(s, labels[i].name) == 0) {
        *value = labels[i].ip; return 0;
    }
    return -1;
}

static int tokenize(char *s, char **t, unsigned max) {
    char *p, *save = NULL;
    unsigned n = 0;
    for (p = strtok_r(s, " \t,\r\n", &save); p != NULL;
         p = strtok_r(NULL, " \t,\r\n", &save)) {
        if (n == max) return -1;
        t[n++] = p;
    }
    return (int)n;
}

static int instruction(char *line, const alabel *labels, unsigned label_count,
                       br_insn *in) {
    char *t[8], *dot;
    int n, op, mode, r;
    n = tokenize(line, t, 8);
    if (n < 2) return -1;
    dot = strchr(t[0], '.');
    if (dot == NULL) return -1;
    *dot++ = 0;
    op = named(t[0], ops, BR_OPCODES); mode = named(dot, modes, 4);
    if (op < 0 || mode < 0) return -1;
    memset(in, 0, sizeof(*in)); in->op = (uint8_t)op; in->mode = (uint8_t)mode;
#define CAP(I) do { r=regno(t[I],'C'); if(r<0)return -1; in->cap=(uint8_t)r; } while(0)
#define REG(F,I) do { r=regno(t[I],'R'); if(r<0)return -1; in->F=(uint8_t)r; } while(0)
    if (op == BR_NOP || op == BR_HALT) { if (n != 2) return -1; CAP(1); }
    else if (op == BR_MOVI) { if(n!=4)return -1; REG(rd,1); if(number(t[2],&in->imm))return -1; CAP(3); }
    else if (op == BR_MOV) { if(n!=4)return -1; REG(rd,1); REG(ra,2); CAP(3); }
    else if (op >= BR_JMP && op <= BR_JNZ) { if(n!=3||label_value(t[1],labels,label_count,&in->imm))return -1; CAP(2); }
    else if ((op >= BR_ADD && op <= BR_XOR) || op == BR_CMP) {
        if(n!=5)return -1;
        REG(rd,1); REG(ra,2); REG(rb,3); CAP(4);
    } else if (op == BR_NOT) { if(n!=4)return -1; REG(rd,1); REG(ra,2); CAP(3); }
    else if (op == BR_SHL || op == BR_SHR) {
        if(n!=5)return -1;
        REG(rd,1); REG(ra,2); if(number(t[3],&in->imm))return -1; CAP(4);
    } else if (op == BR_LOAD) {
        if(n!=4)return -1;
        REG(rd,1); if(number(t[2],&in->imm))return -1; CAP(3);
    } else if (op == BR_STORE) {
        if(n!=4)return -1;
        REG(ra,1); if(number(t[2],&in->imm))return -1; CAP(3);
    } else if (op == BR_PUSH) { if(n!=3)return -1; REG(ra,1); CAP(2); }
    else if (op == BR_POP) { if(n!=3)return -1; REG(rd,1); CAP(2); }
    else if (op == BR_SVC) {
        if(n!=6 || (r=named(t[1],services,9))<0)return -1;
        in->imm=(uint64_t)r;
        REG(rd,2); REG(ra,3); REG(rb,4); CAP(5);
    } else return -1;
#undef CAP
#undef REG
    return 0;
}

int br_assemble_file(const char *source, const char *image, uint32_t override) {
    char lines[ALINES][LLEN], copy[LLEN], *s, *c, *t[3];
    br_insn code[BR_MAX_CODE], probe;
    alabel labels[LABELS];
    uint8_t data[BR_MEMORY_BYTES], used[BR_MEMORY_BYTES];
    FILE *fp;
    uint32_t version = 0, requested = 0, m;
    unsigned line_count = 0, label_count = 0, count = 0, data_length = 0, i, j;
    int profile = 0, have_version = 0, have_caps = 0, n;
    fp = fopen(source, "rb"); if (fp == NULL) return -1;
    while (line_count < ALINES && fgets(lines[line_count], LLEN, fp) != NULL) {
        if (strchr(lines[line_count], '\n') == NULL && !feof(fp)) { fclose(fp); return -1; }
        ++line_count;
    }
    {int ok=feof(fp)&&!ferror(fp)&&line_count!=0;if(fclose(fp))ok=0;if(!ok)return -1;}
    memset(data,0,sizeof(data)); memset(used,0,sizeof(used));
    for (i=0;i<line_count;++i) {
        memcpy(copy,lines[i],LLEN); c=strpbrk(copy,"#;"); if(c)*c=0; s=trim(copy); if(!*s)continue;
        j=(unsigned)strlen(s); if(s[j-1]==':') {
            if(count>=BR_MAX_CODE||label_count>=LABELS)return -1;
            s[j-1]=0; s=trim(s);
            if(!*s||strlen(s)>=sizeof(labels[0].name))return -1;
            for(j=0;j<label_count;++j)if(strcmp(s,labels[j].name)==0)return -1;
            strcpy(labels[label_count].name,s); labels[label_count++].ip=count; continue;
        }
        if(*s=='.') {
            n=tokenize(s,t,3); if(n<2)return -1;
            if(strcmp(t[0],".profile")==0) { if(n!=2||profile||strcmp(t[1],"SIM_CORE"))return -1; profile=1; }
            else if(strcmp(t[0],".image_version")==0) { uint64_t x; if(n!=2||have_version||number(t[1],&x)||x==0||x>UINT32_MAX)return -1; version=(uint32_t)x; have_version=1; }
            else if(strcmp(t[0],".request_caps")==0) { if(n!=2||have_caps||capmask(t[1],&m))return -1; requested=m; have_caps=1; }
            else if(strcmp(t[0],".data")==0) {
                uint64_t off; size_t z; int hi,lo; if(n!=3||number(t[1],&off))return -1;
                z=strlen(t[2]); if((z&1u)||off>BR_MEMORY_BYTES||z/2>BR_MEMORY_BYTES-off)return -1;
                for(j=0;j<z/2;++j) { char a=t[2][j*2],b=t[2][j*2+1];
                    hi=isdigit((unsigned char)a)?a-'0':toupper((unsigned char)a)-'A'+10;
                    lo=isdigit((unsigned char)b)?b-'0':toupper((unsigned char)b)-'A'+10;
                    if(hi<0||hi>15||lo<0||lo>15||used[off+j])return -1;
                    data[off+j]=(uint8_t)((hi<<4)|lo); used[off+j]=1;
                }
                if(off+z/2>data_length)data_length=(unsigned)(off+z/2);
            } else return -1;
        } else ++count;
    }
    if(!profile||!have_version||!have_caps||count==0||count>BR_MAX_CODE)return -1;
    count=0;
    for(i=0;i<line_count;++i) {
        memcpy(copy,lines[i],LLEN); c=strpbrk(copy,"#;"); if(c)*c=0; s=trim(copy); if(!*s||*s=='.')continue;
        j=(unsigned)strlen(s); if(s[j-1]==':')continue;
        if(instruction(s,labels,label_count,&probe)!=0)return -1;
        code[count++]=probe;
    }
    if(override!=0)version=override;
    return br_image_write(image,code,count,data,data_length,version,requested);
}

int br_assembler_selftest(void) {
    static const char good[] =
        ".profile SIM_CORE\n.image_version 3\n.request_caps CONTROL|ARITH|MEMORY|STACK|SERVICE|STATE|UPDATE|DIAG\n.data 0 616263\n"
        "start:\nNOP.WRAP C0\nMOVI.WRAP R0,40,C0\nMOV.WRAP R1,R0,C0\nJMP.WRAP end,C0\nJZ.WRAP end,C0\nJNZ.WRAP start,C0\nHALT.WRAP C0\n"
        "ADD.CHECKED R2,R0,R1,C0\nSUB.WRAP R2,R0,R1,C0\nMUL.SATURATE R2,R0,R1,C0\nDIVU.TRAPPING R2,R0,R1,C0\nMODU.WRAP R2,R0,R1,C0\n"
        "AND.WRAP R2,R0,R1,C0\nOR.WRAP R2,R0,R1,C0\nXOR.WRAP R2,R0,R1,C0\nNOT.WRAP R2,R0,C0\nSHL.WRAP R2,R0,1,C0\nSHR.WRAP R2,R0,1,C0\n"
        "CMP.WRAP R2,R0,R1,C0\nLOAD.WRAP R2,0,C0\nSTORE.WRAP R2,8,C0\nPUSH.WRAP R2,C0\nPOP.WRAP R3,C0\nSVC.WRAP SHA256,R2,R0,R1,C0\nend:\nHALT.WRAP C0\n";
    static const char bad[] = ".profile SIM_CORE\n.image_version 3\n.request_caps CONTROL\nJMP.WRAP missing,C0\n";
    char s[96],a[96],b[96]; FILE *fp,*fa,*fb; int ok=0,x,y;
    snprintf(s,sizeof(s),"/tmp/brasm-%ld.mssl",(long)getpid());
    snprintf(a,sizeof(a),"/tmp/brasm-%ld-a.brimg",(long)getpid());
    snprintf(b,sizeof(b),"/tmp/brasm-%ld-b.brimg",(long)getpid());
    fp=fopen(s,"wb"); if(!fp)return 0;
    if(fwrite(good,1,sizeof(good)-1,fp)!=sizeof(good)-1||fclose(fp))goto done;
    if(br_assemble_file(s,a,3)||br_assemble_file(s,b,3))goto done;
    fa=fopen(a,"rb");fb=fopen(b,"rb");if(!fa||!fb)goto done;
    do{x=fgetc(fa);y=fgetc(fb);if(x!=y){fclose(fa);fclose(fb);goto done;}}while(x!=EOF);
    if(fclose(fa)||fclose(fb))goto done;
    fp=fopen(s,"wb"); if(!fp)goto done;
    if(fwrite(bad,1,sizeof(bad)-1,fp)!=sizeof(bad)-1||fclose(fp))goto done;
    if(br_assemble_file(s,b,3)==0)goto done;
    ok=1;
done: unlink(s); unlink(a); unlink(b); return ok;
}
