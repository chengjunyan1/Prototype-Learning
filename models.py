import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.nn.init as init

from torch.autograd import Variable
from pytorch_metric_learning import distances


""" CIFAR """

class LambdaLayer(nn.Module):
    def __init__(self, lambd):
        super(LambdaLayer, self).__init__()
        self.lambd = lambd
    def forward(self, x): return self.lambd(x)

class BasicBlock(nn.Module):
    expansion = 1
    def __init__(self, in_planes, planes, stride=1, option='A'):
        super(BasicBlock, self).__init__()
        self.conv1 = nn.Conv2d(in_planes, planes, kernel_size=3, stride=stride, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(planes)
        self.conv2 = nn.Conv2d(planes, planes, kernel_size=3, stride=1, padding=1, bias=False)
        self.bn2 = nn.BatchNorm2d(planes)
        self.shortcut = nn.Sequential()
        if stride != 1 or in_planes != planes:
            if option == 'A': # For CIFAR10 ResNet paper uses option A.
                self.shortcut = LambdaLayer(lambda x: F.pad(x[:, :, ::2, ::2], (0, 0, 0, 0, planes//4, planes//4), "constant", 0))
            elif option == 'B':
                self.shortcut = nn.Sequential(
                     nn.Conv2d(in_planes, self.expansion * planes, kernel_size=1, stride=stride, bias=False),
                     nn.BatchNorm2d(self.expansion * planes))

    def forward(self, x):
        out = F.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        out += self.shortcut(x)
        out = F.relu(out)
        return out

class ResNet(nn.Module):
    def __init__(self, num_blocks=[3,3,3], block=BasicBlock, num_classes=10): #default resnet20
        super(ResNet, self).__init__()
        self.in_planes = 16
        self.conv1 = nn.Conv2d(3, 16, kernel_size=3, stride=1, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(16)
        self.layer1 = self._make_layer(block, 16, num_blocks[0], stride=1)
        self.layer2 = self._make_layer(block, 32, num_blocks[1], stride=2)
        self.layer3 = self._make_layer(block, 64, num_blocks[2], stride=2)
        self.apply(_weights_init)

    def _make_layer(self, block, planes, num_blocks, stride):
        strides = [stride] + [1]*(num_blocks-1)
        layers = []
        for stride in strides:
            layers.append(block(self.in_planes, planes, stride))
            self.in_planes = planes * block.expansion
        return nn.Sequential(*layers)

    def forward(self, x):
        out = F.relu(self.bn1(self.conv1(x)))
        out = self.layer1(out)
        out = self.layer2(out)
        out = self.layer3(out)
        out = F.avg_pool2d(out, out.size()[3])
        out = out.view(out.size(0), -1)
        return out

class ResNet20(nn.Module):
    def __init__(self):
        super(ResNet20, self).__init__()
        self.resnet=ResNet()
        self.linear = nn.Linear(64, 10)
        self.criterion = nn.CrossEntropyLoss().cuda()
        self.apply(_weights_init)
    def forward(self, x, embed=False):
        x=self.resnet(x)
        pred=self.linear(x)
        if embed: return pred,x
        return pred
    def loss(self,x,y): return self.criterion(x, y)

class ResNet20DCE(nn.Module):
    def __init__(self):
        super(ResNet20DCE, self).__init__()
        self.resnet=ResNet()
        self.criterion = nn.CrossEntropyLoss()
        self.preluip1 = nn.PReLU()
        self.ip1 = nn.Linear(64, 2)
        self.dce=dce_loss(10,2)
        self.apply(_weights_init)
    def forward(self, x, embed=False,scale=2):
        x=self.resnet(x)
        features = self.preluip1(self.ip1(x))
        centers,distance=self.dce(features)
        if embed: return features,centers,distance #features, centers,distance
        return distance
    def loss(self,distance,label,features,centers,reg=0.001):  
        loss1 = self.criterion(distance, label)
        loss2=regularization(features, centers, label)
        return loss1+reg*loss2

class ResNet20ML(nn.Module):
    def __init__(self,mlloss,D=None):
        super(ResNet20ML, self).__init__()
        self.resnet=ResNet()
        self.linear = nn.Linear(64, 10)
        self.criterion = nn.CrossEntropyLoss().cuda()
        self.mlloss=mlloss
        self.apply(_weights_init)
    def forward(self, x, embed=False):
        x=self.resnet(x)
        pred=self.linear(x)
        if embed: return pred,x
        return pred
    def loss(self,pred,embeds,y,a=0.3,b=1e-4): 
        return self.criterion(pred, y)+self.mlloss(embeds,y,a,b)

class ResNet20PL(nn.Module):
    def __init__(self,lossdist,normdist,preddist,C=1,D=None):
        super(ResNet20PL, self).__init__()
        self.resnet=ResNet()
        self.lin=None if D is None else nn.Linear(64,D)
        D=64 if D is None else D
        self.pl=PL(lossdist,normdist,preddist,D,10,C)
        self.loss=self.pl.loss
        self.apply(_weights_init)
    def forward(self, x, embed=False):
        if self.lin is None: x=self.resnet(x)
        else: x=self.lin(self.resnet(x))
        pred,distance=self.pl.pred(x)
        if not embed: return pred
        return pred,distance,x


""" MNIST """

class ConvNet(nn.Module):
    def __init__(self):
        super(ConvNet, self).__init__()
        self.conv1_1 = nn.Conv2d(1, 32, kernel_size=5, padding=2)
        self.bn1_1 = nn.BatchNorm2d(32)
        self.prelu1_1=nn.PReLU()
        self.conv1_2 = nn.Conv2d(32, 32, kernel_size=5, padding=2)
        self.bn1_2 = nn.BatchNorm2d(32)
        self.prelu1_2=nn.PReLU()
        self.conv2_1 = nn.Conv2d(32, 64, kernel_size=5, padding=2)
        self.bn2_1 = nn.BatchNorm2d(64)
        self.prelu2_1=nn.PReLU()
        self.conv2_2 = nn.Conv2d(64, 64, kernel_size=5, padding=2)
        self.bn2_2 = nn.BatchNorm2d(64)
        self.prelu2_2=nn.PReLU()
        self.conv3_1 = nn.Conv2d(64, 128, kernel_size=5, padding=2)
        self.bn3_1 = nn.BatchNorm2d(128)
        self.prelu3_1=nn.PReLU()
        self.conv3_2 = nn.Conv2d(128, 128, kernel_size=5, padding=2)
        self.bn3_2 = nn.BatchNorm2d(128)
        self.prelu3_2=nn.PReLU()
        self.lin = nn.Linear(128 * 3 * 3, 512)
        self.prelu=nn.PReLU()
        self.emb = nn.Linear(512, 64)

    def forward(self, x, emb=True):
        x = self.prelu1_1(self.bn1_1(self.conv1_1(x)))
        x = self.prelu1_2(self.bn1_2(self.conv1_2(x)))
        x = F.max_pool2d(x, 2)
        x = self.prelu2_1(self.bn2_1(self.conv2_1(x)))
        x = self.prelu2_2(self.bn2_2(self.conv2_2(x)))
        x = F.max_pool2d(x, 2)
        x = self.prelu3_1(self.bn3_1(self.conv3_1(x)))
        x = self.prelu3_2(self.bn3_2(self.conv3_2(x)))
        x = F.max_pool2d(x, 2)
        x= x.view(-1, 128 * 3 * 3)
        if emb: x=self.emb(self.prelu(self.lin(x)))
        return x

class Conv6(nn.Module):
    def __init__(self):
        super(Conv6, self).__init__()
        self.conv=ConvNet()
        self.linear=nn.Linear(64,10)
        self.criterion = nn.CrossEntropyLoss().cuda()
        self.apply(_weights_init)
    def forward(self, x, embed=False):
        x=self.conv(x)
        pred=self.linear(x)
        if embed: return pred,x
        return pred
    def loss(self,x,y): return self.criterion(x, y)

class Conv6DCE(nn.Module):
    def __init__(self):
        super(Conv6DCE, self).__init__()
        self.conv=ConvNet()
        self.criterion = nn.CrossEntropyLoss()
        self.preluip1 = nn.PReLU()
        self.ip1 = nn.Linear(128 * 3 * 3, 2)
        self.dce=dce_loss(10,2)
        self.apply(_weights_init)
    def forward(self, x, embed=False, scale=2):
        x=self.conv(x, False)
        features = self.preluip1(self.ip1(x))
        centers,distance=self.dce(features)
        if embed: return features,centers,distance #features, centers,distance
        return distance
    def loss(self,distance,label,features,centers,reg=0.001): 
        loss1 = self.criterion(distance, label)
        loss2=regularization(features, centers, label)
        return loss1+reg*loss2

class Conv6ML(nn.Module):
    def __init__(self, mlloss):
        super(Conv6ML, self).__init__()
        self.conv=ConvNet()
        self.linear=nn.Linear(64,10)
        self.criterion = nn.CrossEntropyLoss().cuda()
        self.mlloss=mlloss
        self.apply(_weights_init)
    def forward(self, x, embed=False):
        x=self.conv(x)
        pred=self.linear(x)
        if embed: return pred,x
        return pred
    def loss(self,pred,embeds,y,a=0.3,b=1e-4): 
        return self.criterion(pred, y)+self.mlloss(embeds,y,a,b)

class Conv6PL(nn.Module):
    def __init__(self,distance,C=1):
        super(Conv6PL, self).__init__()
        self.conv=ConvNet()
        self.pl=PL(distance,D=64,K=10,C=C)
        self.loss=self.pl.loss
        self.apply(_weights_init)
    def forward(self, x, embed=False):
        x=self.conv(x)
        pred,distance=self.pl.pred(x)
        if not embed: return pred
        return pred,distance,x



""" Utils """

def _weights_init(m):
    if isinstance(m, nn.Linear) or isinstance(m, nn.Conv2d):
        init.kaiming_normal_(m.weight)
        
class dce_loss(torch.nn.Module):
    def __init__(self, n_classes,feat_dim,init_weight=True):
        super(dce_loss, self).__init__()
        self.n_classes=n_classes
        self.feat_dim=feat_dim
        self.centers=nn.Parameter(torch.randn(self.feat_dim,self.n_classes).cuda(),requires_grad=True)
        if init_weight: nn.init.kaiming_normal_(self.centers)
    def forward(self, x):
        features_square=torch.sum(torch.pow(x,2),1, keepdim=True)
        centers_square=torch.sum(torch.pow(self.centers,2),0, keepdim=True)
        features_into_centers=2*torch.matmul(x, (self.centers))
        dist=features_square+centers_square-features_into_centers
        return self.centers, -dist

def regularization(features, centers, labels):
    distance=(features-torch.t(centers)[labels])
    distance=torch.sum(torch.pow(distance,2),1, keepdim=True)
    distance=(torch.sum(distance, 0, keepdim=True))/features.shape[0]
    return distance

class PL(nn.Module):
    def __init__(self,lossdist,normdist='L2',preddist='L2',D=64,K=10,C=2):
        super(PL, self).__init__()
        self.embeds=nn.Parameter(
            torch.randn(C*K,D),requires_grad=True)
        self.C,self.K=C,K
        self.lossdist=distances.LpDistance(power=2) #dist_helper(lossdist)
        self.normdist=distances.LpDistance(power=2) #dist_helper(normdist)
        self.preddist=distances.LpDistance(power=2) #dist_helper(preddist)
        # self.L2dist=distances.LpDistance(power=2)
        self.apply(_weights_init)
    
    def pred(self,x):
        distance=self.lossdist(x, self.embeds)
        distance=distance.reshape(-1,self.C,self.K).mean(1)
        pred=-self.preddist(x, self.embeds)
        pred=pred.reshape(-1,self.C,self.K).mean(1)
        return pred,distance

    def loss(self,pred,x,distance,y,x_adv=None,option=[0.1,0.2]):
        a,b=option # normmode 1 pos 0 neg
        normdist=self.normdist(x,self.embeds)
        normdist=normdist.reshape(-1,self.C,self.K).mean(1)
        plnorm=pl_norm(y,normdist,self.K)
        plloss=pl_loss(y,distance,self.K)
        if x_adv is not None:
            advdist=self.normdist(x_adv,self.embeds)
            advnorm=pl_norm(y,advdist,self.K)
            plloss+=a*advnorm
        # l2norm=l2_norm(x,self.embeds,y,self.K)
        return plloss+b*plnorm#+c*l2norm

def pl_norm(y,dist,K=10,mode=1): # 1 pos 0 neg
    y=torch.nn.functional.one_hot(y,num_classes=K)
    d=gather_nd(dist,y,mode).reshape(-1,1)
    return torch.mean(torch.sum(d,1))

def pl_loss(y,distance,N_class=10): 
    targets=torch.nn.functional.one_hot(y,num_classes=N_class)
    return torch.mean(npair_loss(targets,distance,N_class))

def gather_nd(x,y,w):
    pos=torch.cat(torch.where(y==w)).reshape(2,-1)
    return x[pos[0,:], pos[1,:]]
    
def npair_loss(y,dist,K=10): # CHECKED, IT'S CORRECT
    pos = gather_nd(dist,y,1).reshape(-1,1)
    neg = gather_nd(dist,y,0).reshape(-1,K-1)
    return torch.log(1+torch.sum(torch.exp(pos-neg),-1)) # try absolute

# def fetch_nd(x,y,w):
#     pos=torch.cat(torch.where(y==w)).reshape(2,-1)
#     return x[pos[1,:]]

# def l2_norm(x,p,y,K=10):
#     y=torch.nn.functional.one_hot(y,num_classes=K)
#     pos=fetch_nd(p,y,1)
#     neg=fetch_nd(p,y,0)
#     return (torch.norm(torch.mean(x,0),2)+
#             torch.norm(torch.mean(pos,0),2)+
#             torch.norm(torch.mean(neg,0),2))

def dist_helper(dist):
    assert dist in ['dotproduct','L1','L2','Linf']
    if dist=='dotproduct': return distances.DotProductSimilarity()
    elif dist=='L1': return distances.LpDistance(power=1)
    elif dist=='L2': return distances.LpDistance(power=2)
    elif dist=='Linf': return distances.LpDistance(power=float('inf'))



if __name__ == "__main__":
    m=ResNet()
        
    
        
        
        
