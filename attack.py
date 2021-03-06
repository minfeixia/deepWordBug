import os
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
from torch.autograd import Variable
import argparse
import loaddata
import dataloader
import model
import scoring
import scoring_char
import transformer
import transformer_char
import numpy as np


parser = argparse.ArgumentParser(description='Attack dl model')
parser.add_argument('--data', type=int, default=0, metavar='N',
                    help='data 0 - 8')
parser.add_argument('--length', type=int, default=1014, metavar='N',
                    help='length: default 1014')
parser.add_argument('--model', type=str, default='charcnn', metavar='N',
                    help='model type: LSTM as default')
parser.add_argument('--modelpath', type=str, default='models/charcnn_0_bestmodel.dat', metavar='N',
                    help='model type: LSTM as default')
parser.add_argument('--space', type=bool, default=False, metavar='B',
                    help='Whether including space in the alphabet')
parser.add_argument('--editextra', type=bool, default=False, metavar='B',
                    help='Whether including space in the alphabet')
parser.add_argument('--trans', type=bool, default=False, metavar='B',
                    help='Not implemented yet, add thesausus transformation')
parser.add_argument('--backward', type=int, default=-1, metavar='B',
                    help='Backward direction')
parser.add_argument('--seed', type=int, default=7, metavar='N',
                    help='random seed')
parser.add_argument('--power', type=int, default=0, metavar='B',
                    help='Attack power')
parser.add_argument('--batchsize', type=int, default=128, metavar='B',
                    help='batch size')
parser.add_argument('--scoring', type=str, default='combined', metavar='N',
                    help='Scoring function')
parser.add_argument('--transformer', type=str, default='flip', metavar='N',
                    help='Transformer')
parser.add_argument('--maxbatches', type=int, default=20, metavar='B',
                    help='maximum batches of adv samples generated')
parser.add_argument('--advsamplepath', type=str, default=None, metavar='B',
                    help='advsamplepath')
parser.add_argument('--dictionarysize', type=int, default=20000, metavar='B',
                    help='maximum batches of adv samples generated')
args = parser.parse_args()

np.random.seed(args.seed)
torch.manual_seed(args.seed)
torch.cuda.manual_seed(args.seed)

if args.model == "charcnn":
    args.datatype = "char"
elif args.model == "simplernn":
    args.datatype = "word"
elif args.model == "bilstm":
    args.datatype = "word"

if args.datatype == "char":
    (train,test,numclass) = loaddata.loaddata(args.data)
    traintext = dataloader.Chardata(train,backward = args.backward, length = args.length)
    testtext = dataloader.Chardata(test,backward = args.backward,length = args.length)
elif args.datatype == "word":
    (train,test,tokenizer,numclass) = loaddata.loaddatawithtokenize(args.data,nb_words = args.dictionarysize)
    traintext = dataloader.Worddata(train)
    testtext = dataloader.Worddata(test)

train_loader = DataLoader(traintext,batch_size=args.batchsize, num_workers=4, shuffle = True)
test_loader = DataLoader(testtext,batch_size=args.batchsize, num_workers=4, shuffle = True)

if args.model == "charcnn":
    model = model.CharCNN(classes = numclass)
elif args.model == "simplernn":
    model = model.smallRNN(classes = numclass)
elif args.model == "bilstm":
    model = model.smallRNN(classes = numclass, bidirection = True)

print(model)

state = torch.load(args.modelpath)
model = torch.nn.DataParallel(model).cuda()
model = model.cuda()
try:
    model.load_state_dict(state['state_dict'])
except:
    model = model.module
    model.load_state_dict(state['state_dict'])
    model = torch.nn.DataParallel(model).cuda()

model = model.module
if not os.path.exists('advsamples'):
    os.makedirs('advsamples')

def attackchar(maxbatch = None):
    corrects = .0
    total_loss = 0
    model.eval()
    tgt = []
    adv = []
    origsample = []
    for dataid, data in enumerate(test_loader):
        print dataid
        if maxbatch!=None and dataid >= maxbatch:
            break
        inputs,target = data
        inputs,target = Variable(inputs, volatile=True),  Variable(target)
        inputs, target = inputs.cuda(), target.cuda()
        output = model(inputs)
        tgt.append(target.data)
        origsample.append(inputs.data)
        
        pred = Variable(torch.max(output, 1)[1].view(target.size()).data)
        losses = torch.zeros(inputs.size()[0],inputs.size()[2])
       
        losses = scoring_char.scorefunc(args.scoring)(model, inputs, pred, numclass)
        
        sorted, indices = torch.sort(losses,dim = 1,descending=True)

        advinputs = inputs.data.clone()
        dt = inputs.data.sum(dim=1).int()
        print dt
        for k in xrange(inputs.size()[0]):
            j=0
            t=0
            while j < args.power and t<inputs.size()[1]:
                if dt[k,indices[k][t]]>0:
                    advinputs[k,:,indices[k][t]] = transformer_char.transform(args.transformer)(inputs)
                    j+=1
                t+=1
        adv.append(advinputs)        
        inputs2 = Variable(advinputs, volatile=True)
        output2 = model(inputs2)
        pred2 = torch.max(output2, 1)[1].view(target.size()).data
        corrects += (pred2 == target.data).sum()

    target = torch.cat(tgt)
    advinputs = torch.cat(adv)
    origsamples = torch.cat(origsample)
    acc = corrects/advinputs.size(0)
    print('Accuracy %.5f' % (acc))
    f = open('attack.txt','a')
    f.write('%d\t%s\t%s\t%s\t%d\t%.2f\n' % (args.data,args.model,args.scoring,args.transformer,args.power,100*acc))
    if args.advsamplepath == None:
        advsamplepath = 'advsamples/%s_%d_%s_%s_%d.dat' % (args.model,args.data,args.scoring,args.transformer,args.power)
    else:
        advsamplepath = args.advsamplepath
    torch.save({'original':origsamples,'advinputs':advinputs,'labels':target},advsamplepath)

def attackword(maxbatch = None):
    corrects = .0
    total_loss = 0
    model.eval()
    wordinput = []
    tgt = []
    adv = []
    origsample = []
    flagstore = True
    for dataid, data in enumerate(test_loader):
        print dataid
        if maxbatch!=None and dataid >= maxbatch:
            break
        inputs,target = data
        inputs,target = Variable(inputs, volatile=True),  Variable(target)
        inputs, target = inputs.cuda(), target.cuda()
        origsample.append(inputs.data)
        tgt.append(target.data)
        wtmp = []
        output = model(inputs)
        pred = Variable(torch.max(output, 1)[1].view(target.size()).data)
        
        losses = scoring.scorefunc(args.scoring)(model, inputs, pred, numclass)
        
        sorted, indices = torch.sort(losses,dim = 1,descending=True)
        advinputs = inputs.clone()
        
        if flagstore:
            for k in xrange(inputs.size()[0]):
                wtmp.append([])
                for i in xrange(inputs.size()[1]):
                    if advinputs.data[k,i]>3:
                        wtmp[-1].append(index2word[advinputs.data[k,i]])
                    else:
                        wtmp[-1].append('')
            for k in xrange(inputs.size()[0]):
                j = 0
                t = 0
                while j < args.power and t<inputs.size()[1]:
                    if advinputs.data[k,indices[k][t]]>3:
                        word, advinputs.data[k,indices[k][t]] = transformer.transform(args.transformer)(advinputs[k,indices[k][t]].data[0],word_index,index2word, top_words = args.dictionarysize)
                        wtmp[k][indices[k][t]] = word
                        j+=1
                    t+=1
        else:
            for k in xrange(inputs.size()[0]):
                for i in xrange(args.power):
                    word, advinputs.data[k,indices[k][i]] = transformer.transform(args.transformer)(advinputs[k,indices[k][i]].data[0],word_index,index2word, top_words = args.dictionarysize)
        adv.append(advinputs.data)
        for i in xrange(len(wtmp)):
            wordinput.append(wtmp[i])
        output2 = model(advinputs)
        pred2 = torch.max(output2, 1)[1].view(target.size()).data
        corrects += (pred2 == target.data).sum()
    print wordinput[0]
            
    target = torch.cat(tgt)
    advinputs = torch.cat(adv)
    origsamples = torch.cat(origsample)
    acc = corrects/advinputs.size(0)
    print('Accuracy %.5f' % (acc))
    f = open('attack.txt','a')
    f.write('%d\t%s\t%s\t%s\t%d\t%.2f\n' % (args.data,args.model,args.scoring,args.transformer,args.power,100*acc))
    if args.advsamplepath == None:
        advsamplepath = 'advsamples/%s_%d_%s_%s_%d.dat' % (args.model,args.data,args.scoring,args.transformer,args.power)
    else:
        advsamplepath = args.advsamplepath
    torch.save({'original':origsamples,'wordinput':wordinput,'advinputs':advinputs,'labels':target}, advsamplepath)

    
if args.datatype == "char":
    attackchar(maxbatch = args.maxbatches)
elif args.datatype == "word":
    word_index = tokenizer.word_index
    index2word = {}
    index2word[0] = '[PADDING]'
    index2word[1] = '[START]'
    index2word[2] = '[UNKNOWN]'
    index2word[3] = ''
    if args.dictionarysize==20000:
        for i in word_index:
            if word_index[i]+3<args.dictionarysize:
                index2word[word_index[i]+3]=i
        dirname = os.path.dirname('dict/')
        if not os.path.exists(dirname):
            os.makedirs(dirname)
        torch.save(index2word, 'dict/'+str(args.data)+".dict")
    else:
        for i in word_index:
            if word_index[i] + 3 < args.dictionarysize:
                index2word[word_index[i]+3]=i       
    attackword(maxbatch = args.maxbatches)