/* Compile three times against the actual administrative, production and verifier readers. */
#define main original_cli_main
#include "../vm/BOTTLE_ROCKET_3.0.0_MODEL_OPERATIONAL_110K/host/brctl.c"
#define reader load_file
#undef main
#include <assert.h>
#include <dirent.h>

static int descriptors(void){DIR*d=opendir("/proc/self/fd");int n=0;assert(d);while(readdir(d))n++;assert(!closedir(d));return n;}
static void small_file(const char*p){FILE*f=fopen(p,"wb");assert(f);assert(fwrite("abc",1,3,f)==3);assert(!fclose(f));}
int main(void){
 char directory[]="/tmp/df-small-host-XXXXXX",input[256],linkpath[256],fifo[256],priv[256],pub[256],output[256];
 uint8_t *data=NULL;size_t n=0;int fd,before;struct stat st;
 assert(mkdtemp(directory));
 assert(snprintf(input,sizeof input,"%s/input",directory)>0);
 assert(snprintf(output,sizeof output,"%s/output",directory)>0);
 assert(snprintf(linkpath,sizeof linkpath,"%s/link",directory)>0);
 assert(snprintf(fifo,sizeof fifo,"%s/fifo",directory)>0);
 assert(snprintf(priv,sizeof priv,"%s/private",directory)>0);
 assert(snprintf(pub,sizeof pub,"%s/public",directory)>0);
 small_file(input);assert(!reader(input,&data,&n)&&n==3);free(data);
 assert(!symlink(input,linkpath));assert(!mkfifo(fifo,0600));
 before=descriptors();
 for(int i=0;i<100;i++){
  assert(reader(linkpath,&data,&n)==-1&&data==NULL&&n==0);
  assert(reader(fifo,&data,&n)==-1&&data==NULL&&n==0);
  assert(reader(directory,&data,&n)==-1&&data==NULL&&n==0);
 }
 assert(descriptors()==before);
 fd=open(input,O_WRONLY|O_TRUNC);assert(fd>=0);assert(!ftruncate(fd,60000));assert(!close(fd));
 assert(reader(input,&data,&n)==-1&&data==NULL&&n==0);
#if !defined(TEST_PRODUCTION) && !defined(TEST_VERIFIER)
 small_file(priv);assert(keygen(priv,pub)!=0);
 assert(!reader(priv,&data,&n)&&n==3&&!memcmp(data,"abc",3));free(data);
 assert(access(pub,F_OK)!=0);assert(!unlink(priv));
 assert(!symlink(input,priv));assert(keygen(priv,pub)!=0);assert(!lstat(priv,&st)&&S_ISLNK(st.st_mode));assert(!unlink(priv));
 assert(!keygen(priv,pub));assert(!stat(priv,&st)&&(st.st_mode&0777)==0600);
 assert(!stat(pub,&st)&&st.st_size==32);assert(!unlink(priv));assert(!unlink(pub));
 small_file(pub);assert(keygen(priv,pub)!=0);
 assert(!reader(pub,&data,&n)&&n==3&&!memcmp(data,"abc",3));free(data);
 assert(!stat(priv,&st)&&(st.st_mode&0777)==0600);assert(!unlink(priv));assert(!unlink(pub));
 {br_insn insn={0};insn.op=BR_HALT;assert(br_image_write("/dev/full",&insn,1,NULL,0,1,BR_CAP_CONTROL)==-1);}
#else
 (void)st;
#endif
 /* UINT64_MAX plus a two-byte .data literal must not wrap the bound. */
 {FILE*f=fopen(input,"wb");assert(f);
  assert(fputs(".profile SIM_CORE\n.image_version 3\n.request_caps CONTROL\n.data 18446744073709551615 0102\nHALT.WRAP C0\n",f)>=0);assert(!fclose(f));
  assert(br_assemble_file(input,output,0)!=0);assert(access(output,F_OK)!=0);
 }
 /* Rejected source with more than 512 lines must close its stream. */
 {FILE*f=fopen(input,"wb");assert(f);for(int i=0;i<513;i++)assert(fputs("# line\n",f)>=0);assert(!fclose(f));}
 before=descriptors();
 for(int i=0;i<100;i++)assert(br_assemble_file(input,output,0)!=0);
 assert(descriptors()==before);assert(access(output,F_OK)!=0);
 assert(!unlink(input)&&!unlink(linkpath)&&!unlink(fifo)&&!rmdir(directory));
 puts("HOST_IO_REGRESSIONS_PASS");return 0;
}
