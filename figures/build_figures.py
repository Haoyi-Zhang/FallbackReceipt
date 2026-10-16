"""Python vector diagrams for policy checking and receiver-closed effects."""
import argparse
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle,FancyArrowPatch

plt.rcParams.update({'font.family':'STIXGeneral','mathtext.fontset':'stix',
    'font.size':9.5,'pdf.fonttype':42,'text.usetex':False,'axes.unicode_minus':False})

class Drawing:
    def __init__(self,w,h):
        self.fig=plt.figure(figsize=(w/2.54,h/2.54))
        self.ax=self.fig.add_axes([0,0,1,1],xlim=(0,w),ylim=(0,h))
        self.ax.set_aspect('equal');self.ax.axis('off');self.labels=[]
    def text(self,x,y,s,size=9.5):
        self.labels.append(self.ax.text(x,y,s,ha='center',va='center',
                           fontsize=size,linespacing=1.12,zorder=3))
    def box(self,x,y,w,h,s):
        self.ax.add_patch(Rectangle((x-w/2,y-h/2),w,h,facecolor='white',
                          edgecolor='black',lw=.7,zorder=2));self.text(x,y,s)
    def line(self,pts,arrow=True,dashed=False):
        x,y=zip(*pts);self.ax.plot(x,y,color='black',lw=.7,
                          ls='--' if dashed else '-',zorder=1)
        if arrow:self.ax.add_patch(FancyArrowPatch(pts[-2],pts[-1],
                          arrowstyle='-|>',mutation_scale=9,color='black',lw=.7))
    def save(self,out,name):
        self.fig.canvas.draw();r=self.fig.canvas.get_renderer();e=self.fig.bbox
        for t in self.labels:
            b=t.get_window_extent(r)
            if b.x0<e.x0 or b.x1>e.x1 or b.y0<e.y0 or b.y1>e.y1:
                raise ValueError('Clipped label: '+t.get_text())
        self.fig.savefig(out/(name+'.pdf'),metadata={'Creator':'Python Matplotlib',
                         'Producer':None,'CreationDate':None,'ModDate':None})
        plt.close(self.fig)

def architecture(out):
    d=Drawing(14.5,8.1)
    for x,y,w,s in [(1.8,6.55,3.1,'Score classes\nand envelopes'),
                    (7.2,6.55,3.1,'Finite minimax\nsynthesizer'),
                    (12.6,6.55,3.1,'Separate\ncertificate checker'),
                    (1.8,3.8,3.1,'Local SQL effect\nand terminal row'),
                    (7.2,3.8,3.1,'Receipt-closed\nreceiver'),
                    (12.6,3.8,3.1,'Persistent controller\nlocal reservation'),
                    (7.2,1.05,4.2,'Separate event replayer')]:
        d.box(x,y,w,1.1,s)
    for left,right,label in [(3.35,5.65,'Specification'),(8.75,11.05,'Strategy')]:
        d.line([(left,6.55),(right,6.55)]);d.text((left+right)/2,7.15,label,8.5)
    d.line([(12.6,6.0),(12.6,4.35)]);d.text(13.65,5.15,'Valid\ncertificate',8.5)
    d.line([(11.05,4.0),(8.75,4.0)]);d.text(9.9,4.5,'Execute / close',8.5)
    d.line([(8.75,3.6),(11.05,3.6)]);d.text(9.9,3.05,'Stable receipt',8.5)
    d.line([(5.65,3.8),(3.35,3.8)]);d.text(4.5,4.55,'Atomic\ntransaction',8.5)
    d.line([(12.6,3.25),(12.6,1.05),(9.3,1.05)],dashed=True)
    d.text(11.1,.45,'Controller events',8.5)
    d.line([(7.2,3.25),(7.2,1.6)],dashed=True)
    d.text(8.3,2.45,'Receiver\nrecords',8.5)
    d.save(out,'architecture')

def protocol(out):
    d=Drawing(11.5,6.8)
    d.box(5.75,5.65,3.2,1.1,'UNSEEN\nno terminal row')
    d.box(3.0,3.0,3.2,1.3,'DONE\none effect')
    d.box(8.5,3.0,3.2,1.3,'CANCELED\nno effect')
    d.line([(5.75,5.1),(5.75,4.5)],arrow=False)
    d.line([(5.75,4.5),(3.0,4.5),(3.0,3.65)])
    d.line([(5.75,4.5),(8.5,4.5),(8.5,3.65)])
    d.text(3.2,4.9,'Execute wins',9)
    d.text(8.3,4.9,'Close wins',9)
    for x in (3.0,8.5):
        d.line([(x+.5,2.35),(x+.5,1.75),(x-.5,1.75),(x-.5,2.35)])
        d.text(x,.95,'Repeat\nexecute / close',9)
    d.save(out,'protocol')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--outdir',type=Path,
        default=Path(__file__).resolve().parent)
    out=parser.parse_args().outdir;out.mkdir(parents=True,exist_ok=True)
    for draw in (architecture,protocol):draw(out)
